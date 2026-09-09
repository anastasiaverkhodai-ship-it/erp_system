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
    os.getenv(
        "RUN_POSTGRES_E2E"
    )
    == "1"
)

COMPANY_ID = 1
USER_ID = 1

ZERO = Decimal(
    "0.00"
)


pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run real PostgreSQL chronology"
    ),
)


def _load_fifo_pg_helpers():
    """
    Reuse the already-proven real FIFO PostgreSQL harness.

    This deliberately avoids duplicating:
    - purchase business fixture;
    - real purchase fulfillment;
    - real FIFO StockLot creation;
    - sales reservation;
    - real sales ISSUE;
    - FIFO StockLotConsumption creation;
    - rollback baseline accounting.

    The helper test remains independently executable.
    """

    path = Path(
        __file__
    ).with_name(
        "test_purchase_value_correction_fifo_impact_"
        "postgresql_chronology.py"
    )

    module_name = (
        "_pvc_fifo_impact_pg_helpers_for_gl"
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
            "Could not load FIFO PostgreSQL helper module"
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


fifo = _load_fifo_pg_helpers()


async def rows(
    db,
    sql,
    params=None,
):
    result = await db.execute(
        text(
            sql
        ),
        params or {},
    )

    return tuple(
        result.mappings().all()
    )


async def scalar(
    db,
    sql,
    params=None,
):
    result = await db.execute(
        text(
            sql
        ),
        params or {},
    )

    return result.scalar_one()


async def journal_for_impact(
    db,
    *,
    impact_event_id,
):
    result = await rows(
        db,
        """
        SELECT
            id,
            entry_date,
            status::text AS status,
            reversal_of_id
        FROM journal_entries
        WHERE company_id = :company_id
          AND purchase_value_correction_fifo_impact_event_id
              = :impact_event_id
        ORDER BY id
        """,
        {
            "company_id": COMPANY_ID,
            "impact_event_id": impact_event_id,
        },
    )

    assert len(
        result
    ) == 1, (
        "Expected exactly one typed JournalEntry "
        f"for FIFO impact {impact_event_id}; "
        f"found={result}"
    )

    return result[
        0
    ]


async def journal_posting_by_code(
    db,
    *,
    journal_entry_id,
):
    result = await rows(
        db,
        """
        SELECT
            a.code,
            SUM(
                COALESCE(
                    jel.debit,
                    0
                )
            ) AS debit,
            SUM(
                COALESCE(
                    jel.credit,
                    0
                )
            ) AS credit
        FROM journal_entry_lines jel
        JOIN accounts a
          ON a.id = jel.account_id
        WHERE jel.journal_entry_id =
              :journal_entry_id
        GROUP BY a.code
        ORDER BY a.code
        """,
        {
            "journal_entry_id":
                journal_entry_id,
        },
    )

    return {
        str(
            row[
                "code"
            ]
        ): (
            Decimal(
                row[
                    "debit"
                ]
            ),
            Decimal(
                row[
                    "credit"
                ]
            ),
        )
        for row
        in result
    }


def assert_posting(
    actual,
    expected,
):
    assert set(
        actual
    ) == set(
        expected
    ), (
        "\nUnexpected JournalEntry accounts.\n"
        f"actual={actual}\n"
        f"expected={expected}"
    )

    for code, (
        expected_debit,
        expected_credit,
    ) in expected.items():
        debit, credit = (
            actual[
                code
            ]
        )

        assert Decimal(
            debit
        ) == Decimal(
            expected_debit
        )

        assert Decimal(
            credit
        ) == Decimal(
            expected_credit
        )


async def issue_historical_destination(
    db,
    *,
    issue_document_id,
):
    """
    Resolve the actual historical ISSUE debit account
    directly from the original posted JournalEntry.

    No account 902 hardcode.
    """

    journals = await rows(
        db,
        """
        SELECT
            id,
            entry_date,
            status::text AS status,
            accounting_rule_id
        FROM journal_entries
        WHERE company_id = :company_id
          AND document_id = :document_id
          AND reversal_of_id IS NULL
        ORDER BY id
        """,
        {
            "company_id":
                COMPANY_ID,
            "document_id":
                issue_document_id,
        },
    )

    assert len(
        journals
    ) == 1, (
        "Expected exactly one original ISSUE JournalEntry; "
        f"found={journals}"
    )

    journal = journals[
        0
    ]

    posting = (
        await journal_posting_by_code(
            db,
            journal_entry_id=(
                journal[
                    "id"
                ]
            ),
        )
    )

    debit_destinations = [
        code
        for code, (
            debit,
            credit,
        )
        in posting.items()
        if (
            debit
            > ZERO
            and credit
            == ZERO
        )
    ]

    assert len(
        debit_destinations
    ) == 1, (
        "Historical ISSUE must expose exactly one "
        "positive debit destination; "
        f"posting={posting}"
    )

    destination_code = (
        debit_destinations[
            0
        ]
    )

    assert (
        destination_code
        != "281"
    ), (
        "Historical ISSUE destination cannot be inventory"
    )

    assert (
        "281"
        in posting
    ), (
        "Historical ISSUE must credit inventory account 281"
    )

    inventory_debit, inventory_credit = (
        posting[
            "281"
        ]
    )

    assert inventory_debit == ZERO
    assert inventory_credit > ZERO

    return (
        journal,
        destination_code,
    )


async def immutable_cost_truth_snapshot(
    db,
    *,
    stock_lot_id,
):
    """
    Snapshot immutable historical cost truth.

    to_jsonb(table_row) avoids coupling this E2E to every
    individual costing column while still proving that PVC GL
    does not rewrite any historical cost row.
    """

    lot = await scalar(
        db,
        """
        SELECT to_jsonb(sl)::text
        FROM stock_lots sl
        WHERE sl.id = :stock_lot_id
        """,
        {
            "stock_lot_id":
                stock_lot_id,
        },
    )

    consumptions = await scalar(
        db,
        """
        SELECT
            COALESCE(
                jsonb_agg(
                    to_jsonb(slc)
                    ORDER BY slc.id
                ),
                '[]'::jsonb
            )::text
        FROM stock_lot_consumptions slc
        WHERE slc.stock_lot_id =
              :stock_lot_id
        """,
        {
            "stock_lot_id":
                stock_lot_id,
        },
    )

    inventory_cost_entries = await scalar(
        db,
        """
        SELECT
            COALESCE(
                jsonb_agg(
                    to_jsonb(ice)
                    ORDER BY ice.id
                ),
                '[]'::jsonb
            )::text
        FROM inventory_cost_entries ice
        WHERE ice.company_id =
              :company_id
        """,
        {
            "company_id":
                COMPANY_ID,
        },
    )

    return (
        lot,
        consumptions,
        inventory_cost_entries,
    )


async def pvc_journal_count(
    db,
):
    value = await scalar(
        db,
        """
        SELECT COUNT(*)
        FROM journal_entries
        WHERE company_id =
              :company_id
          AND purchase_value_correction_fifo_impact_event_id
              IS NOT NULL
        """,
        {
            "company_id":
                COMPANY_ID,
        },
    )

    return int(
        value
    )


async def pvc_gl_net_by_code(
    db,
    *,
    allocation_event_id,
):
    """
    Historical postings including reversals.

    Net debit-positive convention:
        SUM(debit - credit)
    """

    result = await rows(
        db,
        """
        SELECT
            a.code,
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
            ) AS net_amount
        FROM purchase_value_correction_fifo_impact_events impact
        JOIN journal_entries je
          ON je.company_id =
             impact.company_id
         AND je.purchase_value_correction_fifo_impact_event_id =
             impact.id
        JOIN journal_entry_lines jel
          ON jel.journal_entry_id =
             je.id
        JOIN accounts a
          ON a.id =
             jel.account_id
        WHERE impact.company_id =
              :company_id
          AND impact.purchase_value_correction_allocation_event_id =
              :allocation_event_id
        GROUP BY a.code
        ORDER BY a.code
        """,
        {
            "company_id":
                COMPANY_ID,
            "allocation_event_id":
                allocation_event_id,
        },
    )

    return {
        str(
            row[
                "code"
            ]
        ): Decimal(
            row[
                "net_amount"
            ]
        )
        for row
        in result
    }


@pytest.mark.asyncio
async def test_purchase_value_correction_fifo_gl_postgresql_chronology():
    """
    REAL PostgreSQL FIFO GL chronology.

    D1:
        purchase receipt 120
        real StockLot created
        Dr281 / Cr631 = 120

    D2:
        PVC economic source 120 -> 108
        initial FIFO destination = 120 on_hand

        typed PVC FIFO GL:
            Dr631 12
            Cr281 12

    D4:
        real sales fulfillment consumes enough existing FIFO
        inventory to reach this receipt lot and consume exactly
        50 from this lot.

        receipt lot:
            original = 120
            consumed = 50
            remaining = 70

        original ISSUE uses its actual historical cost
        destination account.

    D5:
        FIFO topology is reconciled forward-only.

        old:
            on_hand 120 -> 108
            delta = -12

        immutable reversal:
            Dr281 12
            Cr631 12

        replacement issued:
            50 -> 45
            delta = -5

            Dr631 5
            Cr historical ISSUE destination 5

        replacement on_hand:
            70 -> 63
            delta = -7

            Dr631 7
            Cr281 7

        Net current PVC accounting:
            Dr631 12
            Cr historical ISSUE destination 5
            Cr281 7

    Second D5 reconciliation:
        no new impact events
        no new JournalEntries

    Historical StockLot,
    StockLotConsumption and InventoryCostEntry rows:
        unchanged by PVC GL.

    Whole scenario:
        rolled back to exact baseline.
    """

    # pytest-asyncio sibling tests may run on different loops.
    await engine.dispose(
        close=False
    )

    baseline = (
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
                await fifo.base.create_business_fixture(
                    db
                )
            )

            d1 = fixture[
                "business_date"
            ]

            d2 = (
                d1
                + timedelta(
                    days=1
                )
            )

            d4 = (
                d1
                + timedelta(
                    days=3
                )
            )

            d5 = (
                d1
                + timedelta(
                    days=4
                )
            )

            period_end = (
                await fifo.base.open_period_end(
                    db,
                    business_date=d1,
                )
            )

            assert period_end is not None

            if d5 > period_end:
                pytest.skip(
                    "Real PostgreSQL FIFO GL E2E "
                    "requires five usable business "
                    "dates inside the open period"
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
            # A. D1 REAL PURCHASE RECEIPT = 120
            # ==================================================

            receipt = (
                await fifo.execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=(
                        fixture[
                            "order_id"
                        ]
                    ),
                    warehouse_document_number=(
                        "PVC-FIFO-GL-R-"
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
                        fifo.PurchaseOrderFulfillmentRequestLine(
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

            fulfillment_line_id = (
                await fifo.base.fulfillment_line_id(
                    db,
                    fulfillment_id=(
                        receipt
                        .fulfillment
                        .id
                    ),
                )
            )

            lot = (
                await fifo.receipt_lot(
                    db,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                )
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

            allocation = (
                await fifo.create_invoice_fulfillment_allocation(
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
                        fulfillment_line_id
                    ),
                    quantity=Decimal(
                        "120.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            assert allocation.id is not None

            print(
                "D1 REAL RECEIPT 120 "
                "/ STOCKLOT 120 = PASS"
            )

            # ==================================================
            # B. D2 PVC 120 -> 108
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
                    correction_date=d2,
                    original_gross_amount=(
                        "120.00"
                    ),
                    corrected_gross_amount=(
                        "108.00"
                    ),
                    reason_code=(
                        "postgres_fifo_gl_chronology"
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
                    adjustment_date=d2,
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

            # ==================================================
            # C. D2 INITIAL FIFO GL
            # ==================================================

            initial = (
                await reconcile_and_post_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d2,
                    created_by=USER_ID,
                )
            )

            assert len(
                initial.created_events
            ) == 1

            initial_event = (
                initial.created_events[
                    0
                ]
            )

            assert (
                initial_event.reversal_of_id
                is None
            )

            assert (
                initial_event.destination_kind
                == "on_hand"
            )

            assert (
                initial_event.recognition_date
                == d2
            )

            assert Decimal(
                initial_event.original_base_amount
            ) == Decimal(
                "120.00"
            )

            assert Decimal(
                initial_event.corrected_base_amount
            ) == Decimal(
                "108.00"
            )

            initial_journal = (
                await journal_for_impact(
                    db,
                    impact_event_id=(
                        initial_event.id
                    ),
                )
            )

            assert (
                initial_journal[
                    "entry_date"
                ]
                == d2
            )

            assert_posting(
                await journal_posting_by_code(
                    db,
                    journal_entry_id=(
                        initial_journal[
                            "id"
                        ]
                    ),
                ),
                {
                    "281": (
                        ZERO,
                        Decimal(
                            "12.00"
                        ),
                    ),
                    "631": (
                        Decimal(
                            "12.00"
                        ),
                        ZERO,
                    ),
                },
            )

            initial_journal_count = (
                await pvc_journal_count(
                    db
                )
            )

            repeated_d2 = (
                await reconcile_and_post_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d2,
                    created_by=USER_ID,
                )
            )

            assert (
                repeated_d2.created_events
                == ()
            )

            assert (
                await pvc_journal_count(
                    db
                )
                == initial_journal_count
            )

            print(
                "D2 PVC FIFO GL: "
                "Dr631 12 / Cr281 12 "
                "/ SECOND RECONCILE NOOP = PASS"
            )

            # ==================================================
            # D. D4 REAL FIFO ISSUE
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
                        "PVC-FIFO-GL-I-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    document_date=d4,
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

            issue_document_id = (
                issue
                .warehouse_document
                .id
            )

            lot_after_issue = (
                await fifo.receipt_lot(
                    db,
                    fulfillment_line_id=(
                        fulfillment_line_id
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
            ) == Decimal(
                "50.0000"
            )

            (
                issue_journal,
                historical_destination_code,
            ) = (
                await issue_historical_destination(
                    db,
                    issue_document_id=(
                        issue_document_id
                    ),
                )
            )

            assert (
                issue_journal[
                    "entry_date"
                ]
                == d4
            )

            assert (
                historical_destination_code
                not in {
                    "281",
                    "631",
                }
            )

            cost_truth_before_d5 = (
                await immutable_cost_truth_snapshot(
                    db,
                    stock_lot_id=(
                        stock_lot_id
                    ),
                )
            )

            print(
                "D4 REAL FIFO ISSUE: "
                "50 FROM PVC LOT / HISTORICAL DESTINATION = "
                f"{historical_destination_code} = PASS"
            )

            # ==================================================
            # E. D5 FORWARD-ONLY FIFO GL RECLASSIFICATION
            # ==================================================

            d5_result = (
                await reconcile_and_post_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert len(
                d5_result.created_events
            ) == 3

            reversal_events = tuple(
                event
                for event
                in d5_result.created_events
                if (
                    event.reversal_of_id
                    is not None
                )
            )

            replacement_events = tuple(
                event
                for event
                in d5_result.created_events
                if (
                    event.reversal_of_id
                    is None
                )
            )

            assert len(
                reversal_events
            ) == 1

            assert len(
                replacement_events
            ) == 2

            reversal_event = (
                reversal_events[
                    0
                ]
            )

            assert (
                reversal_event.reversal_of_id
                == initial_event.id
            )

            assert (
                reversal_event.recognition_date
                == d5
            )

            replacements_by_kind = {
                event.destination_kind:
                    event
                for event
                in replacement_events
            }

            assert set(
                replacements_by_kind
            ) == {
                "issued",
                "on_hand",
            }

            issued_event = (
                replacements_by_kind[
                    "issued"
                ]
            )

            on_hand_event = (
                replacements_by_kind[
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

            # --------------------------------------------------
            # Initial D2 journal is historical and now REVERSED.
            # --------------------------------------------------

            initial_after_d5 = (
                await journal_for_impact(
                    db,
                    impact_event_id=(
                        initial_event.id
                    ),
                )
            )

            assert (
                str(
                    initial_after_d5[
                        "status"
                    ]
                ).lower()
                == "reversed"
            )

            assert (
                initial_after_d5[
                    "entry_date"
                ]
                == d2
            )

            # --------------------------------------------------
            # D5 typed reversal:
            # Dr281 12 / Cr631 12
            # --------------------------------------------------

            reversal_journal = (
                await journal_for_impact(
                    db,
                    impact_event_id=(
                        reversal_event.id
                    ),
                )
            )

            assert (
                reversal_journal[
                    "entry_date"
                ]
                == d5
            )

            assert (
                reversal_journal[
                    "reversal_of_id"
                ]
                == initial_journal[
                    "id"
                ]
            )

            assert_posting(
                await journal_posting_by_code(
                    db,
                    journal_entry_id=(
                        reversal_journal[
                            "id"
                        ]
                    ),
                ),
                {
                    "281": (
                        Decimal(
                            "12.00"
                        ),
                        ZERO,
                    ),
                    "631": (
                        ZERO,
                        Decimal(
                            "12.00"
                        ),
                    ),
                },
            )

            # --------------------------------------------------
            # D5 issued replacement:
            # Dr631 5 / Cr exact historical ISSUE account 5
            # --------------------------------------------------

            issued_journal = (
                await journal_for_impact(
                    db,
                    impact_event_id=(
                        issued_event.id
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
                issued_journal[
                    "reversal_of_id"
                ]
                is None
            )

            assert_posting(
                await journal_posting_by_code(
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

            # --------------------------------------------------
            # D5 on-hand replacement:
            # Dr631 7 / Cr281 7
            # --------------------------------------------------

            on_hand_journal = (
                await journal_for_impact(
                    db,
                    impact_event_id=(
                        on_hand_event.id
                    ),
                )
            )

            assert (
                on_hand_journal[
                    "entry_date"
                ]
                == d5
            )

            assert (
                on_hand_journal[
                    "reversal_of_id"
                ]
                is None
            )

            assert_posting(
                await journal_posting_by_code(
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

            print(
                "D5 REVERSAL = "
                "Dr281 12 / Cr631 12 = PASS"
            )

            print(
                "D5 ISSUED REPLACEMENT = "
                "Dr631 5 / Cr"
                f"{historical_destination_code} 5 = PASS"
            )

            print(
                "D5 ON-HAND REPLACEMENT = "
                "Dr631 7 / Cr281 7 = PASS"
            )

            # ==================================================
            # F. NET PVC GL
            # ==================================================

            pvc_net = (
                await pvc_gl_net_by_code(
                    db,
                    allocation_event_id=(
                        pvc_allocation.id
                    ),
                )
            )

            assert set(
                pvc_net
            ) == {
                "281",
                "631",
                historical_destination_code,
            }

            assert (
                pvc_net[
                    "631"
                ]
                == Decimal(
                    "12.00"
                )
            )

            assert (
                pvc_net[
                    "281"
                ]
                == Decimal(
                    "-7.00"
                )
            )

            assert (
                pvc_net[
                    historical_destination_code
                ]
                == Decimal(
                    "-5.00"
                )
            )

            print(
                "PVC FIFO CURRENT GL = "
                "Dr631 12 / "
                "Cr281 7 / "
                f"Cr{historical_destination_code} 5 = PASS"
            )

            # ==================================================
            # G. HISTORICAL COST TRUTH MUST NOT CHANGE
            # ==================================================

            cost_truth_after_d5 = (
                await immutable_cost_truth_snapshot(
                    db,
                    stock_lot_id=(
                        stock_lot_id
                    ),
                )
            )

            assert (
                cost_truth_after_d5
                == cost_truth_before_d5
            ), (
                "PVC FIFO GL mutated historical "
                "StockLot / StockLotConsumption / "
                "InventoryCostEntry truth"
            )

            print(
                "STOCKLOT / CONSUMPTION / "
                "INVENTORY COST HISTORY IMMUTABLE = PASS"
            )

            # ==================================================
            # H. SECOND D5 RECONCILE = FULL GL NOOP
            # ==================================================

            journal_count_before_repeat = (
                await pvc_journal_count(
                    db
                )
            )

            repeated_d5 = (
                await reconcile_and_post_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert (
                repeated_d5.created_events
                == ()
            )

            journal_count_after_repeat = (
                await pvc_journal_count(
                    db
                )
            )

            assert (
                journal_count_after_repeat
                == journal_count_before_repeat
            )

            assert (
                journal_count_after_repeat
                == initial_journal_count
                + 3
            )

            duplicate_source_count = (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT
                            purchase_value_correction_fifo_impact_event_id
                        FROM journal_entries
                        WHERE company_id =
                              :company_id
                          AND purchase_value_correction_fifo_impact_event_id
                              IS NOT NULL
                        GROUP BY
                            purchase_value_correction_fifo_impact_event_id
                        HAVING COUNT(*) > 1
                    ) duplicates
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                    },
                )
            )

            assert int(
                duplicate_source_count
            ) == 0

            print(
                "SECOND D5 RECONCILE = "
                "IMPACT NOOP + JOURNAL NOOP = PASS"
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
    # I. EXACT ROLLBACK PROOF
    # ==========================================================

    after = (
        await fifo.complete_table_counts()
    )

    assert (
        after
        == baseline
    ), (
        "\nPostgreSQL PVC FIFO GL rollback "
        "did not restore exact baseline.\n"
        f"before={baseline}\n"
        f"after={after}"
    )

    print(
        "PVC FIFO GL FULL TRANSACTION ROLLBACK = PASS"
    )

    if scenario_error is not None:
        raise scenario_error.with_traceback(
            scenario_traceback
        )
