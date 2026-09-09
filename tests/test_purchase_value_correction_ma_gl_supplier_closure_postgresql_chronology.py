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
from app.models.trade_value_correction_event import (
    TradeValueCorrectionEvent,
)
from app.services.purchase_value_correction_allocation_reconciliation_service import (
    reconcile_purchase_value_correction_allocations_for_event,
)
from app.services.purchase_value_correction_moving_average_lifecycle_service import (
    reconcile_and_post_purchase_value_correction_moving_average_peers,
)
from app.services.reservation_persistence_service import (
    reserve_source_line,
)
from app.services.trade_fulfillment_service import (
    SalesOrderFulfillmentRequestLine,
    execute_sales_order_fulfillment,
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

    spec = importlib.util.spec_from_file_location(
        module_name,
        path,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            f"Could not load helper module: {filename}"
        )

    module = importlib.util.module_from_spec(
        spec
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
        "_pvc_ma_supplier_closure_helpers"
    ),
)

ma = load_test_module(
    filename=(
        "test_purchase_value_correction_moving_average_gl_"
        "postgresql_chronology.py"
    ),
    module_name=(
        "_pvc_ma_gl_supplier_closure_helpers"
    ),
)

base = ma.base


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


async def mapping_one(
    db,
    sql,
    params=None,
):
    return (
        (
            await db.execute(
                text(sql),
                params or {},
            )
        )
        .mappings()
        .one()
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


async def supplier_journal_count(
    db,
    *,
    settlement_id,
):
    return int(
        await scalar(
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
    )


async def ma_journal_count_for_allocation(
    db,
    *,
    allocation_event_id,
):
    return int(
        await scalar(
            db,
            """
            SELECT COUNT(*)
            FROM journal_entries je
            JOIN purchase_value_correction_ma_replay_events replay
              ON replay.company_id =
                 je.company_id
             AND replay.id =
                 je.purchase_value_correction_ma_replay_event_id
            WHERE replay.company_id =
                  :company_id
              AND replay.purchase_value_correction_allocation_event_id =
                  :allocation_event_id
            """,
            {
                "company_id":
                    COMPANY_ID,
                "allocation_event_id":
                    allocation_event_id,
            },
        )
    )


def active_originals(
    history,
):
    reversed_ids = {
        row["reversal_of_id"]
        for row in history
        if row["reversal_of_id"] is not None
    }

    return tuple(
        row
        for row in history
        if (
            row["reversal_of_id"] is None
            and row["id"] not in reversed_ids
        )
    )


async def ma_replay_history(
    db,
    *,
    allocation_event_id,
):
    return await rows(
        db,
        """
        SELECT
            id,
            purchase_value_correction_allocation_event_id,
            effect_kind,
            recognition_date,
            quantity,
            original_valuation_amount,
            corrected_valuation_amount,
            source_moving_average_movement_id,
            source_inventory_cost_entry_id,
            reversal_of_id
        FROM purchase_value_correction_ma_replay_events
        WHERE company_id =
              :company_id
          AND purchase_value_correction_allocation_event_id =
              :allocation_event_id
        ORDER BY id
        """,
        {
            "company_id":
                COMPANY_ID,
            "allocation_event_id":
                allocation_event_id,
        },
    )


async def issue_historical_destination(
    db,
    *,
    issue_document_id,
):
    journal = await mapping_one(
        db,
        """
        SELECT
            je.id,
            je.entry_date
        FROM journal_entries je
        WHERE je.company_id =
              :company_id
          AND je.document_id =
              :document_id
          AND je.reversal_of_id IS NULL
          AND je.purchase_value_correction_ma_replay_event_id
              IS NULL
        ORDER BY je.id
        LIMIT 1
        """,
        {
            "company_id":
                COMPANY_ID,
            "document_id":
                issue_document_id,
        },
    )

    debit_line = await mapping_one(
        db,
        """
        SELECT
            jel.account_id,
            a.code
        FROM journal_entry_lines jel
        JOIN accounts a
          ON a.id =
             jel.account_id
        WHERE jel.journal_entry_id =
              :journal_entry_id
          AND COALESCE(
                  jel.debit,
                  0
              ) > 0
        ORDER BY jel.id
        LIMIT 1
        """,
        {
            "journal_entry_id":
                int(journal["id"]),
        },
    )

    return (
        journal,
        int(
            debit_line[
                "account_id"
            ]
        ),
        str(
            debit_line[
                "code"
            ]
        ),
    )


@pytest.mark.asyncio
async def test_purchase_value_correction_ma_gl_supplier_closure_postgresql_chronology():
    """
    Real PostgreSQL combined supplier + Moving Average PVC closure.

    D1:
        supplier advance payment 120
        purchase receipt 120 @ 1
        supplier advance clearing 120

        371 = 0
        631 = 0

    D3:
        real normal MA ISSUE 50

    D5:
        purchase value correction 120 -> 108

        supplier clearing correction:
            371 = +12
            631 = -12

        MA valuation correction:
            issued:
                50 -> 45
                Dr631 5
                Cr historical ISSUE destination 5

            on_hand:
                70 -> 63
                Dr631 7
                Cr inventory 7

        final:
            631 = 0
            371 = +12

    Base MovingAverageMovement and InventoryCostEntry history
    remain immutable.

    Second supplier + MA reconciliation is a complete NOOP.

    Caller transaction rolls the complete chronology back.
    """

    await engine.dispose(
        close=False
    )

    baseline_counts = (
        await ma.complete_table_counts()
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

            period_end = (
                await supplier.scalar_or_none(
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
            )

            assert period_end is not None

            if d5 > period_end:
                pytest.skip(
                    "Combined PVC MA closure E2E "
                    "requires D1..D5 inside "
                    "the same open period"
                )

            await ma.set_company_to_moving_average(
                db
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

            # ==================================================
            # A. D1 PAYMENT 120
            # ==================================================

            payment = (
                await supplier.confirm_payment(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=fixture[
                        "payment_id"
                    ],
                    confirmed_by=USER_ID,
                )
            )

            settlement = (
                await supplier.create_payment_settlement_allocation(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=payment.id,
                    open_item_id=fixture[
                        "open_item_id"
                    ],
                    amount=Decimal(
                        "120.00"
                    ),
                    created_by=USER_ID,
                )
            )

            assert (
                await supplier.supplier_events(
                    db,
                    settlement_id=settlement.id,
                )
                == []
            )

            # ==================================================
            # B. D1 REAL MOVING-AVERAGE RECEIPT 120 @ 1
            # ==================================================

            receipt = (
                await supplier.execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=fixture[
                        "order_id"
                    ],
                    warehouse_document_number=(
                        "PVC-MA-CLOSE-R-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    document_date=d1,
                    accounting_rule_id=fixture[
                        "accounting_rule_id"
                    ],
                    created_by=USER_ID,
                    request_lines=(
                        supplier.PurchaseOrderFulfillmentRequestLine(
                            trade_document_line_id=fixture[
                                "order_line_id"
                            ],
                            quantity=Decimal(
                                "120.0000"
                            ),
                        ),
                    ),
                )
            )

            await db.flush()

            receipt_line = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        tfl.id AS fulfillment_line_id,
                        tfl.warehouse_document_id,
                        tfl.warehouse_document_line_id,
                        tfl.product_id,
                        tfl.warehouse_id
                    FROM trade_fulfillment_lines tfl
                    WHERE tfl.company_id =
                          :company_id
                      AND tfl.fulfillment_id =
                          :fulfillment_id
                    ORDER BY tfl.id
                    LIMIT 1
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "fulfillment_id":
                            receipt.fulfillment.id,
                    },
                )
            )

            receipt_line_id = int(
                receipt_line[
                    "fulfillment_line_id"
                ]
            )
            product_id = int(
                receipt_line[
                    "product_id"
                ]
            )
            warehouse_id = int(
                receipt_line[
                    "warehouse_id"
                ]
            )
            receipt_document_id = int(
                receipt_line[
                    "warehouse_document_id"
                ]
            )
            receipt_document_line_id = int(
                receipt_line[
                    "warehouse_document_line_id"
                ]
            )

            receipt_movements = (
                await ma.active_ma_movements(
                    db,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            )

            receipt_rows = tuple(
                row
                for row in receipt_movements
                if (
                    int(row["document_id"])
                    == receipt_document_id
                    and int(row["document_line_id"])
                    == receipt_document_line_id
                )
            )

            assert len(
                receipt_rows
            ) == 1

            receipt_ma = (
                receipt_rows[
                    0
                ]
            )

            assert (
                receipt_ma[
                    "movement_type"
                ]
                == "receipt"
            )
            assert ma.money(
                receipt_ma[
                    "quantity_delta"
                ]
            ) == ma.money(
                "120"
            )
            assert ma.money(
                receipt_ma[
                    "value_delta"
                ]
            ) == ma.money(
                "120"
            )
            assert ma.money(
                receipt_ma[
                    "unit_cost"
                ]
            ) == ma.money(
                "1"
            )

            receipt_snapshot = dict(
                receipt_ma
            )

            d1_balance = (
                await ma.ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            )

            assert ma.money(
                d1_balance[
                    "quantity"
                ]
            ) == ma.money(
                "120"
            )
            assert ma.money(
                d1_balance[
                    "inventory_value"
                ]
            ) == ma.money(
                "120"
            )

            # ==================================================
            # C. D1 IFA -> ORIGINAL SUPPLIER CLEARING 120
            # ==================================================

            allocation = (
                await supplier.create_invoice_fulfillment_allocation(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=fixture[
                        "invoice_id"
                    ],
                    invoice_line_id=fixture[
                        "invoice_line_id"
                    ],
                    fulfillment_id=receipt.fulfillment.id,
                    fulfillment_line_id=receipt_line_id,
                    quantity=Decimal(
                        "120.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            initial_supplier_history = (
                await supplier.supplier_events(
                    db,
                    settlement_id=settlement.id,
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
                "D1 PAYMENT + MA RECEIPT + CLEARING: "
                "371 = 0 / 631 = 0 = PASS"
            )

            # ==================================================
            # D. D3 REAL NORMAL MA ISSUE 50
            # ==================================================

            await base.find_or_seed_issue_accounting_rule(
                db
            )

            (
                sales_order_id,
                sales_line_id,
            ) = (
                await base.create_sales_order(
                    db,
                    fixture=fixture,
                    quantity=Decimal(
                        "50.0000"
                    ),
                )
            )

            await db.flush()

            await reserve_source_line(
                db,
                company_id=COMPANY_ID,
                source_document_id=sales_order_id,
                source_document_line_id=sales_line_id,
                quantity=Decimal(
                    "50.0000"
                ),
            )

            await db.flush()

            issue_rule_id = (
                await base.find_or_seed_issue_accounting_rule(
                    db
                )
            )

            issue = (
                await execute_sales_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=sales_order_id,
                    warehouse_document_number=(
                        "PVC-MA-CLOSE-I-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    document_date=d3,
                    accounting_rule_id=issue_rule_id,
                    created_by=USER_ID,
                    request_lines=(
                        SalesOrderFulfillmentRequestLine(
                            trade_document_line_id=sales_line_id,
                            quantity=Decimal(
                                "50.0000"
                            ),
                        ),
                    ),
                )
            )

            await db.flush()

            issue_document_id = int(
                issue.warehouse_document.id
            )

            issue_line = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        tfl.id AS fulfillment_line_id,
                        tfl.warehouse_document_line_id
                    FROM trade_fulfillment_lines tfl
                    WHERE tfl.company_id =
                          :company_id
                      AND tfl.fulfillment_id =
                          :fulfillment_id
                    ORDER BY tfl.id
                    LIMIT 1
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "fulfillment_id":
                            issue.fulfillment.id,
                    },
                )
            )

            issue_document_line_id = int(
                issue_line[
                    "warehouse_document_line_id"
                ]
            )

            issue_movements = (
                await ma.active_ma_movements(
                    db,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            )

            issue_rows = tuple(
                row
                for row in issue_movements
                if (
                    int(row["document_id"])
                    == issue_document_id
                    and int(row["document_line_id"])
                    == issue_document_line_id
                )
            )

            assert len(
                issue_rows
            ) == 1

            issue_ma = (
                issue_rows[
                    0
                ]
            )

            assert (
                issue_ma[
                    "movement_type"
                ]
                == "issue"
            )
            assert ma.money(
                issue_ma[
                    "quantity_delta"
                ]
            ) == ma.money(
                "-50"
            )
            assert ma.money(
                issue_ma[
                    "value_delta"
                ]
            ) == ma.money(
                "-50"
            )

            issue_ma_snapshot = dict(
                issue_ma
            )

            ice = (
                await ma.inventory_cost_entry(
                    db,
                    document_id=issue_document_id,
                    document_line_id=issue_document_line_id,
                )
            )

            ice_snapshot = dict(
                ice
            )

            assert ma.money(
                ice[
                    "quantity"
                ]
            ) == ma.money(
                "50"
            )
            assert ma.money(
                ice[
                    "cost_amount"
                ]
            ) == ma.money(
                "50"
            )

            (
                normal_issue_journal,
                historical_issue_destination_account_id,
                historical_destination_code,
            ) = (
                await issue_historical_destination(
                    db,
                    issue_document_id=issue_document_id,
                )
            )

            assert (
                normal_issue_journal[
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

            d3_balance = (
                await ma.ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            )

            assert ma.money(
                d3_balance[
                    "quantity"
                ]
            ) == ma.money(
                "70"
            )
            assert ma.money(
                d3_balance[
                    "inventory_value"
                ]
            ) == ma.money(
                "70"
            )

            print(
                "D3 REAL MA ISSUE 50 = PASS"
            )
            print(
                "D3 HISTORICAL ISSUE DESTINATION "
                f"{historical_destination_code} = PASS"
            )

            # ==================================================
            # E. D5 ECONOMIC PVC 120 -> 108
            # ==================================================

            correction = (
                TradeValueCorrectionEvent(
                    company_id=COMPANY_ID,
                    direction="purchase",
                    trade_document_id=fixture[
                        "invoice_id"
                    ],
                    trade_document_line_id=fixture[
                        "invoice_line_id"
                    ],
                    product_id=product_id,
                    correction_date=d5,
                    original_gross_amount=Decimal(
                        "120.00"
                    ),
                    original_tax_amount=Decimal(
                        "0.00"
                    ),
                    corrected_gross_amount=Decimal(
                        "108.00"
                    ),
                    corrected_tax_amount=Decimal(
                        "0.00"
                    ),
                    currency_code="UAH",
                    reason_code=(
                        "postgres_combined_631_ma_closure"
                    ),
                    created_by=USER_ID,
                    reversal_of_id=None,
                )
            )

            db.add(
                correction
            )
            await db.flush()

            allocation_result = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=correction.id,
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
            assert ma.money(
                pvc_allocation
                .original_allocated_base_amount
            ) == ma.money(
                "120"
            )
            assert ma.money(
                pvc_allocation
                .corrected_allocated_base_amount
            ) == ma.money(
                "108"
            )
            assert ma.money(
                pvc_allocation
                .allocated_base_delta
            ) == ma.money(
                "-12"
            )

            print(
                "D5 ECONOMIC PVC "
                "120 -> 108 / DELTA -12 = PASS"
            )

            # ==================================================
            # F. D5 SUPPLIER CLEARING CORRECTION
            # ==================================================

            clearing_result = (
                await supplier.reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=fixture[
                        "invoice_id"
                    ],
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

            assert (
                await supplier_journal_count(
                    db,
                    settlement_id=settlement.id,
                )
                == 3
            )

            print(
                "D5 SUPPLIER CLEARING: "
                "371 = +12 / 631 = -12 = PASS"
            )

            # ==================================================
            # G. D5 MA REPLAY + GL — CLOSE 631
            # ==================================================

            ma_result = (
                await reconcile_and_post_purchase_value_correction_moving_average_peers(
                    db,
                    company_id=COMPANY_ID,
                    anchor_allocation_event_id=int(
                        pvc_allocation.id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert len(
                ma_result.created_events
            ) == 2

            created_by_kind = {
                event.effect_kind:
                    event
                for event
                in ma_result.created_events
            }

            assert set(
                created_by_kind
            ) == {
                "issued",
                "on_hand",
            }

            issued_event = (
                created_by_kind[
                    "issued"
                ]
            )
            on_hand_event = (
                created_by_kind[
                    "on_hand"
                ]
            )

            assert ma.money(
                issued_event.quantity
            ) == ma.money(
                "50"
            )
            assert ma.money(
                issued_event.original_valuation_amount
            ) == ma.money(
                "50"
            )
            assert ma.money(
                issued_event.corrected_valuation_amount
            ) == ma.money(
                "45"
            )

            assert (
                int(
                    issued_event
                    .source_moving_average_movement_id
                )
                == int(
                    issue_ma_snapshot[
                        "id"
                    ]
                )
            )
            assert (
                int(
                    issued_event
                    .source_inventory_cost_entry_id
                )
                == int(
                    ice_snapshot[
                        "id"
                    ]
                )
            )

            assert ma.money(
                on_hand_event.quantity
            ) == ma.money(
                "70"
            )
            assert ma.money(
                on_hand_event.original_valuation_amount
            ) == ma.money(
                "70"
            )
            assert ma.money(
                on_hand_event.corrected_valuation_amount
            ) == ma.money(
                "63"
            )

            assert (
                on_hand_event
                .source_moving_average_movement_id
                is None
            )
            assert (
                on_hand_event
                .source_inventory_cost_entry_id
                is None
            )

            history = (
                await ma_replay_history(
                    db,
                    allocation_event_id=int(
                        pvc_allocation.id
                    ),
                )
            )

            active = (
                active_originals(
                    history
                )
            )

            assert len(
                active
            ) == 2

            active_by_kind = {
                row["effect_kind"]:
                    row
                for row in active
            }

            assert set(
                active_by_kind
            ) == {
                "issued",
                "on_hand",
            }

            assert ma.money(
                Decimal(
                    active_by_kind[
                        "issued"
                    ][
                        "corrected_valuation_amount"
                    ]
                )
                - Decimal(
                    active_by_kind[
                        "issued"
                    ][
                        "original_valuation_amount"
                    ]
                )
            ) == ma.money(
                "-5"
            )

            assert ma.money(
                Decimal(
                    active_by_kind[
                        "on_hand"
                    ][
                        "corrected_valuation_amount"
                    ]
                )
                - Decimal(
                    active_by_kind[
                        "on_hand"
                    ][
                        "original_valuation_amount"
                    ]
                )
            ) == ma.money(
                "-7"
            )

            typed_journals = (
                await rows(
                    db,
                    """
                    SELECT
                        je.id,
                        je.entry_date,
                        je.purchase_value_correction_ma_replay_event_id
                    FROM journal_entries je
                    WHERE je.company_id =
                          :company_id
                      AND je.purchase_value_correction_ma_replay_event_id
                          = ANY(:event_ids)
                    ORDER BY je.id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "event_ids": [
                            int(
                                issued_event.id
                            ),
                            int(
                                on_hand_event.id
                            ),
                        ],
                    },
                )
            )

            assert len(
                typed_journals
            ) == 2

            assert all(
                row[
                    "entry_date"
                ]
                == d5
                for row
                in typed_journals
            )

            typed_by_event = {
                int(
                    row[
                        "purchase_value_correction_ma_replay_event_id"
                    ]
                ):
                    row
                for row
                in typed_journals
            }

            issued_je = (
                typed_by_event[
                    int(
                        issued_event.id
                    )
                ]
            )

            issued_lines = (
                await rows(
                    db,
                    """
                    SELECT
                        jel.account_id,
                        a.code,
                        jel.debit,
                        jel.credit
                    FROM journal_entry_lines jel
                    JOIN accounts a
                      ON a.id =
                         jel.account_id
                    WHERE jel.journal_entry_id =
                          :journal_entry_id
                    ORDER BY jel.id
                    """,
                    {
                        "journal_entry_id":
                            int(
                                issued_je[
                                    "id"
                                ]
                            ),
                    },
                )
            )

            issued_credit = tuple(
                line
                for line
                in issued_lines
                if Decimal(
                    line[
                        "credit"
                    ]
                    or 0
                ) > 0
            )

            assert len(
                issued_credit
            ) == 1

            assert (
                int(
                    issued_credit[
                        0
                    ][
                        "account_id"
                    ]
                )
                == historical_issue_destination_account_id
            )

            assert Decimal(
                issued_credit[
                    0
                ][
                    "credit"
                ]
            ) == Decimal(
                "5.00"
            )

            assert (
                int(
                    normal_issue_journal[
                        "id"
                    ]
                )
                < min(
                    int(
                        row[
                            "id"
                        ]
                    )
                    for row
                    in typed_journals
                )
            )

            final_631 = (
                await account_net(
                    db,
                    account_code="631",
                )
                - baseline_631
            )
            final_371 = (
                await account_net(
                    db,
                    account_code="371",
                )
                - baseline_371
            )

            assert (
                final_631
                == ZERO
            )
            assert (
                final_371
                == Decimal(
                    "12.00"
                )
            )

            print(
                "D5 MA GL: "
                "631 = +12 = PASS"
            )
            print(
                "D5 MA ISSUED = -5 / "
                "ON_HAND = -7 = PASS"
            )
            print(
                "HISTORICAL ISSUE DESTINATION "
                f"{historical_destination_code} = PRESERVED"
            )
            print(
                "FINAL 631 = 0 / "
                "371 = +12 = PASS"
            )

            # ==================================================
            # H. BASE MA + ICE HISTORY IMMUTABLE
            # ==================================================

            receipt_after = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        document_id,
                        document_line_id,
                        movement_type,
                        movement_date,
                        quantity_delta,
                        value_delta,
                        unit_cost,
                        balance_quantity_after,
                        balance_value_after,
                        average_unit_cost_after
                    FROM moving_average_movements
                    WHERE id =
                          :movement_id
                    """,
                    {
                        "movement_id":
                            int(
                                receipt_snapshot[
                                    "id"
                                ]
                            ),
                    },
                )
            )

            issue_after = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        document_id,
                        document_line_id,
                        movement_type,
                        movement_date,
                        quantity_delta,
                        value_delta,
                        unit_cost,
                        balance_quantity_after,
                        balance_value_after,
                        average_unit_cost_after
                    FROM moving_average_movements
                    WHERE id =
                          :movement_id
                    """,
                    {
                        "movement_id":
                            int(
                                issue_ma_snapshot[
                                    "id"
                                ]
                            ),
                    },
                )
            )

            for key in (
                "id",
                "document_id",
                "document_line_id",
                "movement_type",
                "movement_date",
                "quantity_delta",
                "value_delta",
                "unit_cost",
                "balance_quantity_after",
                "balance_value_after",
                "average_unit_cost_after",
            ):
                assert (
                    receipt_after[
                        key
                    ]
                    == receipt_snapshot[
                        key
                    ]
                )

                assert (
                    issue_after[
                        key
                    ]
                    == issue_ma_snapshot[
                        key
                    ]
                )

            ice_after = (
                await ma.inventory_cost_entry(
                    db,
                    document_id=issue_document_id,
                    document_line_id=issue_document_line_id,
                )
            )

            assert dict(
                ice_after
            ) == ice_snapshot

            base_balance_after_pvc = (
                await ma.ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            )

            assert ma.money(
                base_balance_after_pvc[
                    "quantity"
                ]
            ) == ma.money(
                "70"
            )
            assert ma.money(
                base_balance_after_pvc[
                    "inventory_value"
                ]
            ) == ma.money(
                "70"
            )

            print(
                "BASE MA / ICE HISTORY IMMUTABLE = PASS"
            )

            # ==================================================
            # I. SECOND D5 RECONCILE = COMPLETE NOOP
            # ==================================================

            supplier_journals_before = (
                await supplier_journal_count(
                    db,
                    settlement_id=settlement.id,
                )
            )

            ma_journals_before = (
                await ma_journal_count_for_allocation(
                    db,
                    allocation_event_id=int(
                        pvc_allocation.id
                    ),
                )
            )

            replay_history_before = (
                await ma_replay_history(
                    db,
                    allocation_event_id=int(
                        pvc_allocation.id
                    ),
                )
            )

            repeat_supplier = (
                await supplier.reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=fixture[
                        "invoice_id"
                    ],
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            repeat_ma = (
                await reconcile_and_post_purchase_value_correction_moving_average_peers(
                    db,
                    company_id=COMPANY_ID,
                    anchor_allocation_event_id=int(
                        pvc_allocation.id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert (
                repeat_supplier.created_events
                == ()
            )
            assert (
                repeat_ma.created_events
                == ()
            )

            assert (
                await supplier_journal_count(
                    db,
                    settlement_id=settlement.id,
                )
                == supplier_journals_before
            )

            assert (
                await ma_journal_count_for_allocation(
                    db,
                    allocation_event_id=int(
                        pvc_allocation.id
                    ),
                )
                == ma_journals_before
            )

            assert (
                await ma_replay_history(
                    db,
                    allocation_event_id=int(
                        pvc_allocation.id
                    ),
                )
                == replay_history_before
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
                "SECOND D5 SUPPLIER + MA "
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
        await ma.complete_table_counts()
    )

    assert (
        after_counts
        == baseline_counts
    ), (
        "\nCombined supplier/MA PVC rollback "
        "did not restore exact baseline.\n"
        f"before={baseline_counts}\n"
        f"after={after_counts}"
    )

    print(
        "COMBINED PVC MA FULL TRANSACTION "
        "ROLLBACK = PASS"
    )

    if scenario_error is not None:
        raise scenario_error.with_traceback(
            scenario_traceback
        )
