import importlib.util
import os
from pathlib import Path
import sys
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import engine
from app.services.purchase_value_correction_fifo_lifecycle_service import (
    reconcile_and_post_purchase_value_correction_fifo_impacts_for_fulfillment_line,
)


RUN_POSTGRES_E2E = (
    os.getenv("RUN_POSTGRES_E2E")
    == "1"
)

COMPANY_ID = 1
USER_ID = 1

ZERO = Decimal("0.00")


pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run real PostgreSQL chronology"
    ),
)


def load_test_module(
    *,
    filename: str,
    module_name: str,
):
    path = (
        Path(__file__)
        .with_name(filename)
    )

    spec = (
        importlib.util.spec_from_file_location(
            module_name,
            path,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            f"Could not load helper module: {filename}"
        )

    module = (
        importlib.util.module_from_spec(
            spec
        )
    )

    sys.modules[
        module_name
    ] = module

    spec.loader.exec_module(
        module
    )

    return module


supplier = load_test_module(
    filename=(
        "test_supplier_advance_clearing_"
        "postgresql_chronology.py"
    ),
    module_name=(
        "_pvc_supplier_closure_helpers"
    ),
)

fifo_gl = load_test_module(
    filename=(
        "test_purchase_value_correction_fifo_gl_"
        "postgresql_chronology.py"
    ),
    module_name=(
        "_pvc_fifo_gl_closure_helpers"
    ),
)

fifo = fifo_gl.fifo


async def scalar(
    db,
    sql,
    params=None,
):
    return (
        await db.execute(
            text(sql),
            params or {},
        )
    ).scalar_one()


async def rows(
    db,
    sql,
    params=None,
):
    return tuple(
        (
            await db.execute(
                text(sql),
                params or {},
            )
        )
        .mappings()
        .all()
    )


async def account_net(
    db,
    *,
    account_code,
):
    value = await scalar(
        db,
        """
        SELECT COALESCE(
            SUM(
                COALESCE(
                    jel.debit,
                    0
                )
                -
                COALESCE(
                    jel.credit,
                    0
                )
            ),
            0
        )
        FROM journal_entry_lines jel
        JOIN journal_entries je
          ON je.id =
             jel.journal_entry_id
        JOIN accounts a
          ON a.id =
             jel.account_id
        WHERE je.company_id =
              :company_id
          AND a.company_id =
              :company_id
          AND a.code =
              :account_code
        """,
        {
            "company_id":
                COMPANY_ID,
            "account_code":
                account_code,
        },
    )

    return Decimal(
        value
    )


async def journal_count_for_fifo_allocation(
    db,
    *,
    allocation_event_id,
):
    value = await scalar(
        db,
        """
        SELECT COUNT(*)
        FROM journal_entries je
        JOIN purchase_value_correction_fifo_impact_events impact
          ON impact.company_id =
             je.company_id
         AND impact.id =
             je.purchase_value_correction_fifo_impact_event_id
        WHERE impact.company_id =
              :company_id
          AND impact.purchase_value_correction_allocation_event_id =
              :allocation_event_id
        """,
        {
            "company_id":
                COMPANY_ID,
            "allocation_event_id":
                allocation_event_id,
        },
    )

    return int(
        value
    )


async def supplier_journal_count(
    db,
    *,
    settlement_id,
):
    value = await scalar(
        db,
        """
        SELECT COUNT(*)
        FROM journal_entries je
        WHERE je.company_id =
              :company_id
          AND je.supplier_advance_clearing_event_id
              IN (
                  SELECT id
                  FROM supplier_advance_clearing_events
                  WHERE company_id =
                        :company_id
                    AND payment_settlement_allocation_id =
                        :settlement_id
              )
        """,
        {
            "company_id":
                COMPANY_ID,
            "settlement_id":
                settlement_id,
        },
    )

    return int(
        value
    )


def active_supplier_originals(
    history,
):
    reversed_ids = {
        row[
            "reversal_of_id"
        ]
        for row
        in history
        if (
            row[
                "reversal_of_id"
            ]
            is not None
        )
    }

    return tuple(
        row
        for row
        in history
        if (
            row[
                "reversal_of_id"
            ]
            is None
            and row[
                "id"
            ]
            not in reversed_ids
        )
    )


@pytest.mark.asyncio
async def test_purchase_value_correction_fifo_gl_supplier_closure_postgresql_chronology():
    """
    One real PostgreSQL accounting chronology.

    D1 payment:
        Dr371 / Cr311 = 120

    D1 purchase receipt:
        Dr281 / Cr631 = 120

    D1 advance clearing:
        Dr631 / Cr371 = 120

    Therefore before PVC:
        371 delta = 0
        631 delta = 0

    D3 real FIFO sales ISSUE:
        consume exactly 50 from the new receipt lot.

        The issue debit account is resolved from the actual
        historical ISSUE JournalEntry, never hardcoded.

    D5 economic PVC:
        120 -> 108
        delta = -12

    D5 supplier clearing correction:
        original clearing 120
        -> reversal 120
        -> replacement 108

        Result before FIFO correction:
            371 = +12
            631 = -12

    D5 FIFO GL:
        issued 50:
            50 -> 45
            Dr631 5
            Cr historical ISSUE destination 5

        on_hand 70:
            70 -> 63
            Dr631 7
            Cr281 7

        FIFO total:
            Dr631 = 12

    Combined final:
        supplier correction:
            Cr631 net 12

        FIFO correction:
            Dr631 net 12

        therefore:
            631 = 0

        371 remains +12 because supplier prepayment
        now exceeds corrected liability by 12.

    No historical costing row is rewritten.
    Second reconciliation is a complete NOOP.
    Entire scenario rolls back exactly.
    """

    await engine.dispose(
        close=False
    )

    baseline_counts = (
        await fifo.complete_table_counts()
    )

    scenario_error = None
    scenario_traceback = None

    async with engine.connect() as connection:
        transaction = (
            await connection.begin()
        )

        db = AsyncSession(
            bind=connection,
            expire_on_commit=False,
        )

        try:
            fixture = (
                await supplier.create_business_fixture(
                    db
                )
            )

            d1 = fixture[
                "business_date"
            ]

            d3 = (
                d1
                + timedelta(
                    days=2
                )
            )

            d5 = (
                d1
                + timedelta(
                    days=4
                )
            )

            period_end = await supplier.scalar_or_none(
                db,
                """
                SELECT end_date
                FROM accounting_periods
                WHERE company_id =
                      :company_id
                  AND status =
                      'open'
                  AND is_locked IS FALSE
                  AND start_date <=
                      :business_date
                  AND end_date >=
                      :business_date
                ORDER BY
                    start_date DESC,
                    id DESC
                LIMIT 1
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "business_date":
                        d1,
                },
            )

            assert period_end is not None

            if d5 > period_end:
                pytest.skip(
                    "Combined PVC closure E2E "
                    "requires D1..D5 inside "
                    "the same open period"
                )

            baseline_371 = (
                await account_net(
                    db,
                    account_code="371",
                )
            )

            baseline_631 = (
                await account_net(
                    db,
                    account_code="631",
                )
            )

            old_fifo_quantity = (
                await fifo.preexisting_fifo_quantity(
                    db,
                    product_id=(
                        fixture[
                            "product_id"
                        ]
                    ),
                    warehouse_id=(
                        fixture[
                            "warehouse_id"
                        ]
                    ),
                )
            )

            # ==================================================
            # A. D1 PAYMENT 120
            # ==================================================

            payment = (
                await supplier.confirm_payment(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=(
                        fixture[
                            "payment_id"
                        ]
                    ),
                    confirmed_by=USER_ID,
                )
            )

            settlement = (
                await supplier.create_payment_settlement_allocation(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=(
                        payment.id
                    ),
                    open_item_id=(
                        fixture[
                            "open_item_id"
                        ]
                    ),
                    amount=Decimal(
                        "120.00"
                    ),
                    created_by=USER_ID,
                )
            )

            before_receipt_events = (
                await supplier.supplier_events(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            assert (
                before_receipt_events
                == []
            )

            # ==================================================
            # B. D1 REAL RECEIPT 120
            # ==================================================

            receipt = (
                await supplier.execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=(
                        fixture[
                            "order_id"
                        ]
                    ),
                    warehouse_document_number=(
                        "PVC-CLOSE-R-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    document_date=d1,
                    accounting_rule_id=(
                        fixture[
                            "accounting_rule_id"
                        ]
                    ),
                    created_by=USER_ID,
                    request_lines=(
                        supplier.PurchaseOrderFulfillmentRequestLine(
                            trade_document_line_id=(
                                fixture[
                                    "order_line_id"
                                ]
                            ),
                            quantity=Decimal(
                                "120.0000"
                            ),
                        ),
                    ),
                )
            )

            receipt_line_id = (
                await fifo.base.fulfillment_line_id(
                    db,
                    fulfillment_id=(
                        receipt
                        .fulfillment
                        .id
                    ),
                )
            )

            lot = await fifo.receipt_lot(
                db,
                fulfillment_line_id=(
                    receipt_line_id
                ),
            )

            stock_lot_id = int(
                lot[
                    "id"
                ]
            )

            assert Decimal(
                lot[
                    "original_quantity"
                ]
            ) == Decimal(
                "120.0000"
            )

            assert Decimal(
                lot[
                    "remaining_quantity"
                ]
            ) == Decimal(
                "120.0000"
            )

            # ==================================================
            # C. D1 IFA => ORIGINAL CLEARING 120
            # ==================================================

            allocation = (
                await supplier.create_invoice_fulfillment_allocation(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=(
                        fixture[
                            "invoice_id"
                        ]
                    ),
                    invoice_line_id=(
                        fixture[
                            "invoice_line_id"
                        ]
                    ),
                    fulfillment_id=(
                        receipt
                        .fulfillment
                        .id
                    ),
                    fulfillment_line_id=(
                        receipt_line_id
                    ),
                    quantity=Decimal(
                        "120.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            initial_supplier_history = (
                await supplier.supplier_events(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            assert len(
                initial_supplier_history
            ) == 1

            original_clearing = (
                initial_supplier_history[
                    0
                ]
            )

            assert (
                original_clearing[
                    "reversal_of_id"
                ]
                is None
            )

            assert Decimal(
                original_clearing[
                    "cleared_amount"
                ]
            ) == Decimal(
                "120.00"
            )

            assert (
                original_clearing[
                    "clearing_date"
                ]
                == d1
            )

            assert (
                await account_net(
                    db,
                    account_code="371",
                )
                - baseline_371
                == ZERO
            )

            assert (
                await account_net(
                    db,
                    account_code="631",
                )
                - baseline_631
                == ZERO
            )

            print(
                "D1 PAYMENT + RECEIPT + CLEARING: "
                "371 = 0 / 631 = 0 = PASS"
            )

            # ==================================================
            # D. D3 REAL FIFO ISSUE — 50 FROM THIS LOT
            # ==================================================

            issue_quantity = (
                old_fifo_quantity
                + Decimal(
                    "50.0000"
                )
            )

            (
                sales_order_id,
                sales_line_id,
            ) = (
                await fifo.create_sales_order(
                    db,
                    fixture=fixture,
                    quantity=(
                        issue_quantity
                    ),
                )
            )

            await fifo.reserve_source_line(
                db,
                company_id=COMPANY_ID,
                source_document_id=(
                    sales_order_id
                ),
                source_document_line_id=(
                    sales_line_id
                ),
                quantity=(
                    issue_quantity
                ),
            )

            issue_rule_id = (
                await fifo.find_or_seed_issue_accounting_rule(
                    db
                )
            )

            issue = (
                await fifo.execute_sales_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=(
                        sales_order_id
                    ),
                    warehouse_document_number=(
                        "PVC-CLOSE-I-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    document_date=d3,
                    accounting_rule_id=(
                        issue_rule_id
                    ),
                    created_by=USER_ID,
                    request_lines=(
                        fifo.SalesOrderFulfillmentRequestLine(
                            trade_document_line_id=(
                                sales_line_id
                            ),
                            quantity=(
                                issue_quantity
                            ),
                        ),
                    ),
                )
            )

            lot_after_issue = (
                await fifo.receipt_lot(
                    db,
                    fulfillment_line_id=(
                        receipt_line_id
                    ),
                )
            )

            assert Decimal(
                lot_after_issue[
                    "remaining_quantity"
                ]
            ) == Decimal(
                "70.0000"
            )

            assert (
                await fifo.active_consumed_quantity(
                    db,
                    stock_lot_id=(
                        stock_lot_id
                    ),
                )
                == Decimal(
                    "50.0000"
                )
            )

            (
                issue_journal,
                historical_destination_code,
            ) = (
                await fifo_gl.issue_historical_destination(
                    db,
                    issue_document_id=(
                        issue
                        .warehouse_document
                        .id
                    ),
                )
            )

            assert (
                issue_journal[
                    "entry_date"
                ]
                == d3
            )

            assert (
                historical_destination_code
                not in {
                    "281",
                    "371",
                    "631",
                }
            )

            historical_cost_truth = (
                await fifo_gl.immutable_cost_truth_snapshot(
                    db,
                    stock_lot_id=(
                        stock_lot_id
                    ),
                )
            )

            print(
                "D3 REAL FIFO ISSUE: "
                "50 FROM PVC LOT / HISTORICAL DESTINATION "
                f"{historical_destination_code} = PASS"
            )

            # ==================================================
            # E. D5 ECONOMIC PVC 120 -> 108
            # ==================================================

            correction = (
                await fifo.base.insert_value_correction(
                    db,
                    invoice_id=(
                        fixture[
                            "invoice_id"
                        ]
                    ),
                    invoice_line_id=(
                        fixture[
                            "invoice_line_id"
                        ]
                    ),
                    product_id=(
                        fixture[
                            "product_id"
                        ]
                    ),
                    correction_date=d5,
                    original_gross_amount=(
                        "120.00"
                    ),
                    corrected_gross_amount=(
                        "108.00"
                    ),
                    reason_code=(
                        "postgres_combined_631_fifo_closure"
                    ),
                )
            )

            allocation_result = (
                await fifo.reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction.id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert len(
                allocation_result.created_events
            ) == 1

            pvc_allocation = (
                allocation_result
                .created_events[
                    0
                ]
            )

            assert (
                pvc_allocation
                .invoice_fulfillment_allocation_id
                == allocation.id
            )

            assert Decimal(
                pvc_allocation
                .original_allocated_base_amount
            ) == Decimal(
                "120.00"
            )

            assert Decimal(
                pvc_allocation
                .corrected_allocated_base_amount
            ) == Decimal(
                "108.00"
            )

            assert (
                pvc_allocation
                .allocated_base_delta
                == Decimal(
                    "-12.00"
                )
            )

            print(
                "D5 ECONOMIC PVC: "
                "120 -> 108 / DELTA -12 = PASS"
            )

            # ==================================================
            # F. D5 SUPPLIER CLEARING CORRECTION
            # ==================================================

            clearing_result = (
                await supplier.reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=(
                        fixture[
                            "invoice_id"
                        ]
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert len(
                clearing_result.created_events
            ) == 2

            clearing_reversal = (
                clearing_result
                .created_events[
                    0
                ]
            )

            clearing_replacement = (
                clearing_result
                .created_events[
                    1
                ]
            )

            assert (
                clearing_reversal.reversal_of_id
                == original_clearing[
                    "id"
                ]
            )

            assert (
                clearing_reversal.clearing_date
                == d5
            )

            assert Decimal(
                clearing_reversal.cleared_amount
            ) == Decimal(
                "120.00"
            )

            assert (
                clearing_replacement.reversal_of_id
                is None
            )

            assert (
                clearing_replacement.clearing_date
                == d5
            )

            assert Decimal(
                clearing_replacement.cleared_amount
            ) == Decimal(
                "108.00"
            )

            supplier_history = (
                await supplier.supplier_events(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            assert len(
                supplier_history
            ) == 3

            active_supplier = (
                active_supplier_originals(
                    supplier_history
                )
            )

            assert len(
                active_supplier
            ) == 1

            assert Decimal(
                active_supplier[
                    0
                ][
                    "cleared_amount"
                ]
            ) == Decimal(
                "108.00"
            )

            after_supplier_371 = (
                await account_net(
                    db,
                    account_code="371",
                )
                - baseline_371
            )

            after_supplier_631 = (
                await account_net(
                    db,
                    account_code="631",
                )
                - baseline_631
            )

            assert (
                after_supplier_371
                == Decimal(
                    "12.00"
                )
            )

            assert (
                after_supplier_631
                == Decimal(
                    "-12.00"
                )
            )

            supplier_count_before_fifo = (
                await supplier_journal_count(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            assert (
                supplier_count_before_fifo
                == 3
            )

            print(
                "D5 SUPPLIER CLEARING CORRECTION: "
                "371 = +12 / 631 = -12 = PASS"
            )

            # ==================================================
            # G. D5 FIFO GL — CLOSE THE 631 DIFFERENCE
            # ==================================================

            fifo_result = (
                await reconcile_and_post_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        receipt_line_id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert len(
                fifo_result.created_events
            ) == 2

            assert all(
                event.reversal_of_id
                is None
                for event
                in fifo_result.created_events
            )

            fifo_by_kind = {
                event.destination_kind:
                    event
                for event
                in fifo_result.created_events
            }

            assert set(
                fifo_by_kind
            ) == {
                "issued",
                "on_hand",
            }

            issued_event = (
                fifo_by_kind[
                    "issued"
                ]
            )

            on_hand_event = (
                fifo_by_kind[
                    "on_hand"
                ]
            )

            assert (
                issued_event.recognition_date
                == d5
            )

            assert (
                on_hand_event.recognition_date
                == d5
            )

            assert Decimal(
                issued_event.quantity
            ) == Decimal(
                "50.0000"
            )

            assert Decimal(
                issued_event.original_base_amount
            ) == Decimal(
                "50.00"
            )

            assert Decimal(
                issued_event.corrected_base_amount
            ) == Decimal(
                "45.00"
            )

            assert Decimal(
                on_hand_event.quantity
            ) == Decimal(
                "70.0000"
            )

            assert Decimal(
                on_hand_event.original_base_amount
            ) == Decimal(
                "70.00"
            )

            assert Decimal(
                on_hand_event.corrected_base_amount
            ) == Decimal(
                "63.00"
            )

            issued_journal = (
                await fifo_gl.journal_for_impact(
                    db,
                    impact_event_id=(
                        issued_event.id
                    ),
                )
            )

            on_hand_journal = (
                await fifo_gl.journal_for_impact(
                    db,
                    impact_event_id=(
                        on_hand_event.id
                    ),
                )
            )

            assert (
                issued_journal[
                    "entry_date"
                ]
                == d5
            )

            assert (
                on_hand_journal[
                    "entry_date"
                ]
                == d5
            )

            fifo_gl.assert_posting(
                await fifo_gl.journal_posting_by_code(
                    db,
                    journal_entry_id=(
                        issued_journal[
                            "id"
                        ]
                    ),
                ),
                {
                    historical_destination_code: (
                        ZERO,
                        Decimal(
                            "5.00"
                        ),
                    ),
                    "631": (
                        Decimal(
                            "5.00"
                        ),
                        ZERO,
                    ),
                },
            )

            fifo_gl.assert_posting(
                await fifo_gl.journal_posting_by_code(
                    db,
                    journal_entry_id=(
                        on_hand_journal[
                            "id"
                        ]
                    ),
                ),
                {
                    "281": (
                        ZERO,
                        Decimal(
                            "7.00"
                        ),
                    ),
                    "631": (
                        Decimal(
                            "7.00"
                        ),
                        ZERO,
                    ),
                },
            )

            pvc_fifo_net = (
                await fifo_gl.pvc_gl_net_by_code(
                    db,
                    allocation_event_id=(
                        pvc_allocation.id
                    ),
                )
            )

            assert set(
                pvc_fifo_net
            ) == {
                "281",
                "631",
                historical_destination_code,
            }

            assert (
                pvc_fifo_net[
                    "631"
                ]
                == Decimal(
                    "12.00"
                )
            )

            assert (
                pvc_fifo_net[
                    "281"
                ]
                == Decimal(
                    "-7.00"
                )
            )

            assert (
                pvc_fifo_net[
                    historical_destination_code
                ]
                == Decimal(
                    "-5.00"
                )
            )

            final_371 = (
                await account_net(
                    db,
                    account_code="371",
                )
                - baseline_371
            )

            final_631 = (
                await account_net(
                    db,
                    account_code="631",
                )
                - baseline_631
            )

            assert (
                final_371
                == Decimal(
                    "12.00"
                )
            )

            assert (
                final_631
                == ZERO
            )

            print(
                "D5 FIFO GL: "
                "Dr631 12 / "
                "Cr281 7 / "
                f"Cr{historical_destination_code} 5 = PASS"
            )

            print(
                "COMBINED PVC CLOSURE: "
                "SUPPLIER Cr631 12 + "
                "FIFO Dr631 12 => "
                "631 = 0 = PASS"
            )

            print(
                "SUPPLIER ADVANCE EXCESS: "
                "371 = +12 = PASS"
            )

            # ==================================================
            # H. COST HISTORY IMMUTABILITY
            # ==================================================

            after_fifo_cost_truth = (
                await fifo_gl.immutable_cost_truth_snapshot(
                    db,
                    stock_lot_id=(
                        stock_lot_id
                    ),
                )
            )

            assert (
                after_fifo_cost_truth
                == historical_cost_truth
            )

            print(
                "HISTORICAL FIFO COST TRUTH "
                "UNCHANGED BY PVC GL = PASS"
            )

            # ==================================================
            # I. SECOND D5 RECONCILES = COMPLETE NOOP
            # ==================================================

            supplier_journals_before_repeat = (
                await supplier_journal_count(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            fifo_journals_before_repeat = (
                await journal_count_for_fifo_allocation(
                    db,
                    allocation_event_id=(
                        pvc_allocation.id
                    ),
                )
            )

            repeat_supplier = (
                await supplier.reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=(
                        fixture[
                            "invoice_id"
                        ]
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            repeat_fifo = (
                await reconcile_and_post_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        receipt_line_id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_supplier.created_events
                == ()
            )

            assert (
                repeat_fifo.created_events
                == ()
            )

            assert (
                await supplier_journal_count(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
                == supplier_journals_before_repeat
            )

            assert (
                await journal_count_for_fifo_allocation(
                    db,
                    allocation_event_id=(
                        pvc_allocation.id
                    ),
                )
                == fifo_journals_before_repeat
            )

            assert (
                await account_net(
                    db,
                    account_code="631",
                )
                - baseline_631
                == ZERO
            )

            assert (
                await account_net(
                    db,
                    account_code="371",
                )
                - baseline_371
                == Decimal(
                    "12.00"
                )
            )

            print(
                "SECOND D5 SUPPLIER + FIFO "
                "RECONCILE = FULL NOOP = PASS"
            )

        except BaseException as exc:
            scenario_error = exc
            scenario_traceback = (
                exc.__traceback__
            )

        finally:
            await db.close()

            if transaction.is_active:
                await transaction.rollback()

    # ==========================================================
    # J. EXACT TRANSACTION ROLLBACK
    # ==========================================================

    after_counts = (
        await fifo.complete_table_counts()
    )

    assert (
        after_counts
        == baseline_counts
    ), (
        "\nCombined supplier/FIFO PVC rollback "
        "did not restore exact baseline.\n"
        f"before={baseline_counts}\n"
        f"after={after_counts}"
    )

    print(
        "COMBINED PVC FULL TRANSACTION "
        "ROLLBACK = PASS"
    )

    if scenario_error is not None:
        raise scenario_error.with_traceback(
            scenario_traceback
        )
