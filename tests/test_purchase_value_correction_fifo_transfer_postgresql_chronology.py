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


RUN_POSTGRES_E2E = (
    os.getenv("RUN_POSTGRES_E2E")
    == "1"
)

COMPANY_ID = 1
USER_ID = 1

PURCHASE_QTY = Decimal("120.0000")
ROUTED_QTY = Decimal("50.0000")


pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run real FIFO Transfer↔PVC "
        "PostgreSQL chronology"
    ),
)


def _load_module(
    filename,
    module_name,
):
    path = Path(__file__).with_name(
        filename
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
            f"Cannot load {filename}"
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


fifo = _load_module(
    "test_purchase_value_correction_fifo_impact_"
    "postgresql_chronology.py",
    "_fifo_transfer_pvc_pg_fifo",
)

wt = _load_module(
    "test_warehouse_transfer_fifo_postgresql_"
    "chronology.py",
    "_fifo_transfer_pvc_pg_wt",
)


async def rows(
    db,
    sql,
    params=None,
):
    result = await db.execute(
        text(sql),
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
        text(sql),
        params or {},
    )

    return result.scalar_one()


async def transfer_pvc_baseline():
    return {
        "fifo": (
            await fifo.complete_table_counts()
        ),
        "transfer": (
            await wt.transfer_table_counts()
        ),
    }


@pytest.mark.asyncio
async def test_fifo_transfer_pvc_on_hand_postgresql_chronology():
    """
    REAL PostgreSQL FIFO Transfer↔PVC chronology.

    D1
      Real purchase receipt = 120.

    D2
      Transfer:
        all pre-existing FIFO stock
        + 50 units from the new purchase lot.

      Therefore the new purchase lot becomes:
        source on_hand = 70
        transferred = 50.

    D3
      Purchase Value Correction:
        120.00 -> 108.00.

      The transfer ISSUE is not final economic consumption.

      Expected ACTIVE FIFO PVC destinations:

        source purchase StockLot:
          on_hand 70
          70.00 -> 63.00

        destination transfer StockLot:
          on_hand 50
          50.00 -> 45.00

      Total:
          quantity 120
          original 120.00
          corrected 108.00

    WTVL must remain immutable.

    The transfer documents themselves must have no
    JournalEntry.

    Reconciliation must be idempotent.

    Entire scenario is rolled back by caller transaction.
    """

    await engine.dispose(
        close=False
    )

    baseline = (
        await transfer_pvc_baseline()
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
            # ==================================================
            # A. REAL BUSINESS FIXTURE
            # ==================================================

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
                + timedelta(days=1)
            )

            d3 = (
                d1
                + timedelta(days=2)
            )

            period_end = fixture.get(
                "period_end"
            )

            if (
                period_end is not None
                and d3 > period_end
            ):
                pytest.skip(
                    "FIFO Transfer↔PVC PostgreSQL "
                    "chronology requires three usable "
                    "business dates in the open period"
                )

            product_id = int(
                fixture[
                    "product_id"
                ]
            )

            source_warehouse_id = int(
                fixture[
                    "warehouse_id"
                ]
            )

            destination_warehouse_id = int(
                await scalar(
                    db,
                    """
                    SELECT id
                    FROM warehouses
                    WHERE company_id = :company_id
                      AND is_active IS TRUE
                      AND id <> :source_warehouse_id
                    ORDER BY id
                    LIMIT 1
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "source_warehouse_id": (
                            source_warehouse_id
                        ),
                    },
                )
            )

            company_method = await scalar(
                db,
                """
                SELECT inventory_valuation_method
                FROM companies
                WHERE id = :company_id
                """,
                {
                    "company_id": COMPANY_ID,
                },
            )

            assert str(
                company_method
            ) == "fifo"

            print(
                "FIFO TRANSFER↔PVC COMPANY "
                f"source={source_warehouse_id} "
                f"destination={destination_warehouse_id} "
                "= PASS"
            )

            # ==================================================
            # B. PRE-EXISTING FIFO QUANTITY
            # ==================================================

            old_fifo_quantity = (
                await fifo.preexisting_fifo_quantity(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        source_warehouse_id
                    ),
                )
            )

            assert (
                old_fifo_quantity
                >= Decimal("0")
            )

            transfer_quantity = (
                old_fifo_quantity
                + ROUTED_QTY
            )

            # ==================================================
            # C. D1 REAL PURCHASE RECEIPT = 120
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
                                        "PVC-WT-FIFO-PG-R-"
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

            await db.flush()

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

            source_lot = (
                await fifo.receipt_lot(
                    db,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                )
            )

            source_lot_id = int(
                source_lot[
                    "id"
                ]
            )

            assert Decimal(
                source_lot[
                    "original_quantity"
                ]
            ) == PURCHASE_QTY

            assert Decimal(
                source_lot[
                    "remaining_quantity"
                ]
            ) == PURCHASE_QTY

            print(
                "D1 PURCHASE LOT 120 = PASS"
            )

            # ==================================================
            # D. INVOICE ↔ FULFILLMENT ALLOCATION
            # ==================================================

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

            await db.flush()

            assert allocation.id is not None

            # ==================================================
            # E. D2 REAL WAREHOUSE TRANSFER
            # ==================================================

            history_key = (
                str(
                    __import__("uuid").uuid4()
                )
            )

            target = (
                wt.normalize_transfer_target(
                                company_id=COMPANY_ID,
                                source_warehouse_id=(
                                    source_warehouse_id
                                ),
                                destination_warehouse_id=(
                                    destination_warehouse_id
                                ),
                                transfer_date=d2,
                                lines=(
                                    wt.WarehouseTransferLineTarget(
                                        product_id=product_id,
                                        quantity=transfer_quantity,
                                    ),
                                ),
                            )
            )

            create_plan = (
                await wt.execute_warehouse_transfer_reconciliation(
                                    db,
                                    company_id=COMPANY_ID,
                                    history_key=history_key,
                                    target=target,
                                    adjustment_date=None,
                                    created_by=USER_ID,
                                )
            )

            await db.flush()

            assert (
                create_plan.action.value
                == "create"
            )

            transfer_event = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        history_key,
                        source_warehouse_id,
                        destination_warehouse_id,
                        transfer_date
                    FROM warehouse_transfer_events
                    WHERE company_id = :company_id
                      AND history_key = :history_key
                    ORDER BY id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "history_key": (
                            history_key
                        ),
                    },
                )
            )

            assert len(
                transfer_event
            ) == 1

            transfer_event_id = int(
                transfer_event[0][
                    "id"
                ]
            )

            transfer_lines = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        product_id,
                        quantity,
                        issue_document_id,
                        issue_document_line_id,
                        receipt_document_id
                    FROM warehouse_transfer_lines
                    WHERE company_id = :company_id
                      AND transfer_event_id = :event_id
                    ORDER BY id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "event_id": (
                            transfer_event_id
                        ),
                    },
                )
            )

            assert len(
                transfer_lines
            ) == 1

            transfer_line = (
                transfer_lines[
                    0
                ]
            )

            issue_document_id = int(
                transfer_line[
                    "issue_document_id"
                ]
            )

            transfer_receipt_document_id = int(
                transfer_line[
                    "receipt_document_id"
                ]
            )

            # ==================================================
            # F. EXACT WTVL ROUTE FOR OUR PURCHASE LOT
            # ==================================================

            routed_layers = (
                await rows(
                    db,
                    """
                    SELECT
                        wtvl.id,
                        wtvl.transfer_line_id,
                        wtvl.source_inventory_cost_entry_id,
                        wtvl.source_stock_lot_consumption_id,
                        wtvl.destination_receipt_document_id,
                        wtvl.destination_receipt_document_line_id,
                        wtvl.quantity,
                        wtvl.unit_cost,
                        wtvl.valuation_amount
                    FROM warehouse_transfer_valuation_layers wtvl
                    JOIN stock_lot_consumptions slc
                      ON slc.company_id =
                         wtvl.company_id
                     AND slc.id =
                         wtvl.source_stock_lot_consumption_id
                    WHERE wtvl.company_id = :company_id
                      AND wtvl.transfer_line_id = :transfer_line_id
                      AND slc.stock_lot_id = :stock_lot_id
                    ORDER BY wtvl.id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "transfer_line_id": int(
                            transfer_line[
                                "id"
                            ]
                        ),
                        "stock_lot_id": (
                            source_lot_id
                        ),
                    },
                )
            )

            assert len(
                routed_layers
            ) == 1

            routed_layer = (
                routed_layers[
                    0
                ]
            )

            assert Decimal(
                routed_layer[
                    "quantity"
                ]
            ) == ROUTED_QTY

            destination_line_id = int(
                routed_layer[
                    "destination_receipt_document_line_id"
                ]
            )

            destination_lots = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        warehouse_id,
                        original_quantity,
                        remaining_quantity,
                        unit_cost,
                        source_document_id,
                        source_document_line_id
                    FROM stock_lots
                    WHERE company_id = :company_id
                      AND source_document_id =
                          :receipt_document_id
                      AND source_document_line_id =
                          :receipt_document_line_id
                    ORDER BY id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "receipt_document_id": (
                            transfer_receipt_document_id
                        ),
                        "receipt_document_line_id": (
                            destination_line_id
                        ),
                    },
                )
            )

            assert len(
                destination_lots
            ) == 1

            destination_lot = (
                destination_lots[
                    0
                ]
            )

            destination_lot_id = int(
                destination_lot[
                    "id"
                ]
            )

            assert int(
                destination_lot[
                    "warehouse_id"
                ]
            ) == destination_warehouse_id

            assert Decimal(
                destination_lot[
                    "original_quantity"
                ]
            ) == ROUTED_QTY

            assert Decimal(
                destination_lot[
                    "remaining_quantity"
                ]
            ) == ROUTED_QTY

            source_after_transfer = (
                await fifo.receipt_lot(
                    db,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                )
            )

            assert Decimal(
                source_after_transfer[
                    "remaining_quantity"
                ]
            ) == Decimal(
                "70.0000"
            )

            # Preserve immutable snapshots before PVC.
            wtvl_snapshot = dict(
                routed_layer
            )

            source_consumption_snapshot = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        stock_lot_id,
                        issue_document_id,
                        issue_document_line_id,
                        quantity,
                        unit_cost
                    FROM stock_lot_consumptions
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "id": int(
                            routed_layer[
                                "source_stock_lot_consumption_id"
                            ]
                        ),
                    },
                )
            )

            assert len(
                source_consumption_snapshot
            ) == 1

            source_consumption_snapshot = dict(
                source_consumption_snapshot[
                    0
                ]
            )

            source_ice_snapshot = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        document_id,
                        document_line_id,
                        quantity,
                        unit_cost,
                        cost_amount,
                        valuation_amount
                    FROM inventory_cost_entries
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "id": int(
                            routed_layer[
                                "source_inventory_cost_entry_id"
                            ]
                        ),
                    },
                )
            )

            assert len(
                source_ice_snapshot
            ) == 1

            source_ice_snapshot = dict(
                source_ice_snapshot[
                    0
                ]
            )

            print(
                "D2 TRANSFER ROUTE "
                "SOURCE LOT → SLC → WTVL "
                "→ DESTINATION LOT = PASS"
            )

            # ==================================================
            # G. TRANSFER HAS NO ACCOUNTING JOURNAL
            # ==================================================

            transfer_journal_count = int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM journal_entries
                    WHERE company_id = :company_id
                      AND document_id IN (
                          :issue_document_id,
                          :receipt_document_id
                      )
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "issue_document_id": (
                            issue_document_id
                        ),
                        "receipt_document_id": (
                            transfer_receipt_document_id
                        ),
                    },
                )
            )

            assert (
                transfer_journal_count
                == 0
            )

            print(
                "TRANSFER JOURNAL ENTRY = ZERO PASS"
            )

            # ==================================================
            # H. D3 PURCHASE VALUE CORRECTION
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
                                    correction_date=d3,
                                    original_gross_amount=(
                                        "120.00"
                                    ),
                                    corrected_gross_amount=(
                                        "108.00"
                                    ),
                                    reason_code=(
                                        "postgres_fifo_transfer_on_hand"
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
                    adjustment_date=d3,
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

            fifo.assert_money(
                pvc_allocation
                .original_allocated_base_amount,
                "120.00",
            )

            fifo.assert_money(
                pvc_allocation
                .corrected_allocated_base_amount,
                "108.00",
            )

            impact_result = (
                await fifo.reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d3,
                    created_by=USER_ID,
                )
            )

            assert len(
                impact_result.created_events
            ) == 2

            impact_rows = (
                await fifo.impact_history(
                    db,
                    allocation_event_id=(
                        pvc_allocation.id
                    ),
                )
            )

            active = (
                fifo.active_impact_rows(
                    impact_rows
                )
            )

            assert len(
                active
            ) == 2

            by_lot = {
                int(
                    row[
                        "stock_lot_id"
                    ]
                ): row
                for row in active
            }

            assert set(
                by_lot
            ) == {
                source_lot_id,
                destination_lot_id,
            }

            source_impact = (
                by_lot[
                    source_lot_id
                ]
            )

            destination_impact = (
                by_lot[
                    destination_lot_id
                ]
            )

            # --------------------------------------------------
            # Source warehouse 70 remains physically on hand.
            # --------------------------------------------------

            assert (
                source_impact[
                    "destination_kind"
                ]
                == "on_hand"
            )

            assert (
                source_impact[
                    "stock_lot_consumption_id"
                ]
                is None
            )

            assert Decimal(
                source_impact[
                    "quantity"
                ]
            ) == Decimal(
                "70.0000"
            )

            fifo.assert_money(
                source_impact[
                    "original_base_amount"
                ],
                "70.00",
            )

            fifo.assert_money(
                source_impact[
                    "corrected_base_amount"
                ],
                "63.00",
            )

            # --------------------------------------------------
            # Transferred 50 routes to destination StockLot.
            # It MUST NOT remain an issued PVC destination.
            # --------------------------------------------------

            assert (
                destination_impact[
                    "destination_kind"
                ]
                == "on_hand"
            )

            assert (
                destination_impact[
                    "stock_lot_consumption_id"
                ]
                is None
            )

            assert Decimal(
                destination_impact[
                    "quantity"
                ]
            ) == ROUTED_QTY

            fifo.assert_money(
                destination_impact[
                    "original_base_amount"
                ],
                "50.00",
            )

            fifo.assert_money(
                destination_impact[
                    "corrected_base_amount"
                ],
                "45.00",
            )

            assert (
                destination_impact[
                    "recognition_date"
                ]
                == d3
            )

            assert (
                source_impact[
                    "recognition_date"
                ]
                == d3
            )

            total_quantity = sum(
                (
                    Decimal(
                        row[
                            "quantity"
                        ]
                    )
                    for row in active
                ),
                Decimal("0"),
            )

            total_original = sum(
                (
                    Decimal(
                        row[
                            "original_base_amount"
                        ]
                    )
                    for row in active
                ),
                Decimal("0"),
            )

            total_corrected = sum(
                (
                    Decimal(
                        row[
                            "corrected_base_amount"
                        ]
                    )
                    for row in active
                ),
                Decimal("0"),
            )

            assert (
                total_quantity
                == PURCHASE_QTY
            )

            assert (
                total_original
                == Decimal("120.00")
            )

            assert (
                total_corrected
                == Decimal("108.00")
            )

            print(
                "FIFO PVC ROUTING: "
                "70 SOURCE ON_HAND + "
                "50 DESTINATION ON_HAND = PASS"
            )

            print(
                "FIFO PVC MONEY CONSERVATION "
                "120.00 → 108.00 = PASS"
            )

            # ==================================================
            # I. IDEMPOTENCY
            # ==================================================

            repeat = (
                await fifo.reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d3,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat.created_events
                == ()
            )

            print(
                "FIFO TRANSFER↔PVC IDEMPOTENCY = PASS"
            )

            # ==================================================
            # J. IMMUTABLE TRANSFER / COST PROVENANCE
            # ==================================================

            routed_layer_after = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        transfer_line_id,
                        source_inventory_cost_entry_id,
                        source_stock_lot_consumption_id,
                        destination_receipt_document_id,
                        destination_receipt_document_line_id,
                        quantity,
                        unit_cost,
                        valuation_amount
                    FROM warehouse_transfer_valuation_layers
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "id": int(
                            wtvl_snapshot[
                                "id"
                            ]
                        ),
                    },
                )
            )

            assert len(
                routed_layer_after
            ) == 1

            assert dict(
                routed_layer_after[
                    0
                ]
            ) == wtvl_snapshot

            source_consumption_after = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        stock_lot_id,
                        issue_document_id,
                        issue_document_line_id,
                        quantity,
                        unit_cost
                    FROM stock_lot_consumptions
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "id": int(
                            source_consumption_snapshot[
                                "id"
                            ]
                        ),
                    },
                )
            )

            assert len(
                source_consumption_after
            ) == 1

            assert dict(
                source_consumption_after[
                    0
                ]
            ) == source_consumption_snapshot

            source_ice_after = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        document_id,
                        document_line_id,
                        quantity,
                        unit_cost,
                        cost_amount,
                        valuation_amount
                    FROM inventory_cost_entries
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "id": int(
                            source_ice_snapshot[
                                "id"
                            ]
                        ),
                    },
                )
            )

            assert len(
                source_ice_after
            ) == 1

            assert dict(
                source_ice_after[
                    0
                ]
            ) == source_ice_snapshot

            destination_lot_after = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        warehouse_id,
                        original_quantity,
                        remaining_quantity,
                        unit_cost,
                        source_document_id,
                        source_document_line_id
                    FROM stock_lots
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "id": destination_lot_id,
                    },
                )
            )

            assert len(
                destination_lot_after
            ) == 1

            assert dict(
                destination_lot_after[
                    0
                ]
            ) == dict(
                destination_lot
            )

            print(
                "FIFO / ICE / WTVL HISTORY "
                "IMMUTABLE = PASS"
            )

        except BaseException as exc:
            import traceback

            scenario_error = exc
            scenario_traceback = (
                traceback.format_exc()
            )

        finally:
            await db.close()

            if transaction.is_active:
                await transaction.rollback()

    if scenario_error is not None:
        print(
            scenario_traceback
        )

        raise scenario_error

    after = (
        await transfer_pvc_baseline()
    )

    assert after == baseline, (
        "\nFIFO Transfer↔PVC PostgreSQL "
        "rollback did not restore baseline"
        f"\nbefore={baseline}"
        f"\nafter={after}"
    )

    print(
        "FIFO TRANSFER↔PVC OUTER ROLLBACK = PASS"
    )
