import importlib.util
import os
from pathlib import Path
import sys
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import engine
from app.services.warehouse_transfer_history_service import (
    WarehouseTransferLineTarget,
    normalize_transfer_target,
)
from app.services.warehouse_transfer_reconciliation_executor import (
    execute_warehouse_transfer_reconciliation,
)


RUN_POSTGRES_E2E = (
    os.getenv("RUN_POSTGRES_E2E")
    == "1"
)

COMPANY_ID = 1
USER_ID = 1
TRANSFER_QTY = Decimal("50.0000")


pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run real warehouse-transfer FIFO "
        "PostgreSQL chronology"
    ),
)


def _load_fifo_pg_helpers():
    """
    Reuse the already-proven PostgreSQL FIFO business harness.

    It provides:
    - real company / user / product;
    - open accounting period;
    - real purchase business fixture;
    - production purchase fulfillment;
    - real FIFO StockLot creation;
    - complete baseline-count helpers.
    """

    path = Path(__file__).with_name(
        "test_purchase_value_correction_fifo_impact_"
        "postgresql_chronology.py"
    )

    module_name = (
        "_warehouse_transfer_fifo_pg_base"
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
            "Could not load proven FIFO PostgreSQL helper module"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    sys.modules[module_name] = module

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
    return (
        await db.execute(
            text(sql),
            params or {},
        )
    ).scalar_one()


TRANSFER_TABLES = (
    "warehouse_transfer_events",
    "warehouse_transfer_lines",
    "warehouse_transfer_valuation_layers",
)


async def transfer_table_counts():
    values = {}

    async with engine.connect() as connection:
        for table_name in TRANSFER_TABLES:
            values[table_name] = (
                await connection.execute(
                    text(
                        f"""
                        SELECT COUNT(*)
                        FROM {table_name}
                        """
                    )
                )
            ).scalar_one()

    return values


async def complete_baseline():
    return {
        "fifo_base": (
            await fifo.complete_table_counts()
        ),
        "transfer": (
            await transfer_table_counts()
        ),
    }


def decimal(value):
    return Decimal(value)


def expected_fifo_slices(
    lots,
    quantity,
):
    remaining = Decimal(quantity)
    result = []

    for lot in lots:
        if remaining <= Decimal("0"):
            break

        available = Decimal(
            lot["remaining_quantity"]
        )

        if available <= Decimal("0"):
            continue

        consumed = min(
            available,
            remaining,
        )

        result.append(
            {
                "stock_lot_id": int(
                    lot["id"]
                ),
                "quantity": consumed,
                "unit_cost": Decimal(
                    lot["unit_cost"]
                ),
            }
        )

        remaining -= consumed

    assert remaining == Decimal("0"), (
        "Source FIFO snapshot did not contain "
        f"enough stock. Missing={remaining}"
    )

    return tuple(result)


@pytest.mark.asyncio
async def test_warehouse_transfer_fifo_postgresql_chronology():
    """
    REAL PostgreSQL warehouse-transfer FIFO chronology.

    D1
      Real purchase receipt through production fulfillment.
      At least 120 units of FIFO stock exist in source warehouse.

    D2
      Production warehouse-transfer reconciliation:
        source ISSUE
        -> FIFO StockLotConsumption
        -> InventoryCostEntry
        -> exact transfer valuation provenance
        -> destination RECEIPT
        -> exact destination FIFO lots
        -> immutable WarehouseTransferEvent/Line/Layers

    Assertions:
      - source FIFO consumption follows exact historical lot order;
      - WTVL layer == source StockLotConsumption slice;
      - destination lot == WTVL historical layer;
      - quantity/value conservation is exact;
      - ownership transfer creates no fake averaged FIFO layer.

    D3
      Production reconciliation target=None:
        reverse destination RECEIPT first
        -> reverse source ISSUE second
        -> append immutable reversal WarehouseTransferEvent.

    Assertions:
      - destination transfer lots become zero;
      - exact original source lots are restored;
      - original consumption / ICE / WTVL provenance remains immutable;
      - physical documents are REVERSED.

    Finally
      Whole E2E transaction is rolled back and database row-count
      baseline must be exactly restored.
    """

    await engine.dispose(
        close=False
    )

    baseline = await complete_baseline()

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

            period_end = (
                await fifo.base.open_period_end(
                    db,
                    business_date=d1,
                )
            )

            assert period_end is not None

            if d3 > period_end:
                pytest.skip(
                    "Real warehouse-transfer FIFO E2E "
                    "requires three usable business dates "
                    "inside the open period"
                )

            product_id = int(
                fixture["product_id"]
            )

            source_warehouse_id = int(
                fixture["warehouse_id"]
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

            assert (
                destination_warehouse_id
                != source_warehouse_id
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

            assert str(company_method) == "fifo", (
                "Real company must use FIFO for 7G2; "
                f"actual={company_method!r}"
            )

            print(
                "FIFO COMPANY / WAREHOUSES "
                f"source={source_warehouse_id} "
                f"destination={destination_warehouse_id} "
                "= PASS"
            )

            # ==================================================
            # B. D1 REAL PURCHASE RECEIPT = 120
            # ==================================================

            receipt = (
                await fifo.execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=(
                        fixture["order_id"]
                    ),
                    warehouse_document_number=(
                        "WT-FIFO-PG-R-"
                        + fixture["suffix"]
                    ),
                    document_date=d1,
                    accounting_rule_id=(
                        fixture["accounting_rule_id"]
                    ),
                    created_by=USER_ID,
                    request_lines=(
                        fifo.PurchaseOrderFulfillmentRequestLine(
                            trade_document_line_id=(
                                fixture["order_line_id"]
                            ),
                            quantity=Decimal(
                                "120.0000"
                            ),
                        ),
                    ),
                )
            )

            await db.flush()

            receipt_document_id = int(
                receipt.warehouse_document.id
            )

            print(
                "D1 REAL PURCHASE RECEIPT 120 "
                f"document={receipt_document_id} "
                "= PASS"
            )

            # ==================================================
            # C. SOURCE FIFO SNAPSHOT BEFORE TRANSFER
            # ==================================================

            source_lots_before = await rows(
                db,
                """
                SELECT
                    id,
                    source_document_id,
                    source_document_line_id,
                    received_date,
                    original_quantity,
                    remaining_quantity,
                    unit_cost
                FROM stock_lots
                WHERE company_id = :company_id
                  AND product_id = :product_id
                  AND warehouse_id = :warehouse_id
                  AND remaining_quantity > 0
                ORDER BY received_date, id
                """,
                {
                    "company_id": COMPANY_ID,
                    "product_id": product_id,
                    "warehouse_id": (
                        source_warehouse_id
                    ),
                },
            )

            available_before = sum(
                (
                    Decimal(
                        row["remaining_quantity"]
                    )
                    for row in source_lots_before
                ),
                Decimal("0"),
            )

            assert (
                available_before
                >= TRANSFER_QTY
            )

            expected_slices = (
                expected_fifo_slices(
                    source_lots_before,
                    TRANSFER_QTY,
                )
            )

            source_snapshot_by_id = {
                int(row["id"]): {
                    "remaining_quantity": Decimal(
                        row["remaining_quantity"]
                    ),
                    "original_quantity": Decimal(
                        row["original_quantity"]
                    ),
                    "unit_cost": Decimal(
                        row["unit_cost"]
                    ),
                }
                for row in source_lots_before
            }

            expected_value = sum(
                (
                    row["quantity"]
                    * row["unit_cost"]
                    for row in expected_slices
                ),
                Decimal("0"),
            )

            print(
                "SOURCE FIFO SNAPSHOT "
                f"available={available_before} "
                f"expected_layers={len(expected_slices)} "
                f"transfer_value={expected_value} "
                "= PASS"
            )

            # ==================================================
            # D. DESTINATION BASELINE
            # ==================================================

            destination_lots_before = await rows(
                db,
                """
                SELECT
                    id,
                    source_document_id,
                    source_document_line_id,
                    original_quantity,
                    remaining_quantity,
                    unit_cost
                FROM stock_lots
                WHERE company_id = :company_id
                  AND product_id = :product_id
                  AND warehouse_id = :warehouse_id
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "product_id": product_id,
                    "warehouse_id": (
                        destination_warehouse_id
                    ),
                },
            )

            destination_ids_before = {
                int(row["id"])
                for row
                in destination_lots_before
            }

            # ==================================================
            # E. D2 REAL TRANSFER THROUGH PRODUCTION EXECUTOR
            # ==================================================

            history_key = str(
                uuid4()
            )

            target = normalize_transfer_target(
                company_id=COMPANY_ID,
                source_warehouse_id=(
                    source_warehouse_id
                ),
                destination_warehouse_id=(
                    destination_warehouse_id
                ),
                transfer_date=d2,
                lines=(
                    WarehouseTransferLineTarget(
                        product_id=product_id,
                        quantity=TRANSFER_QTY,
                    ),
                ),
            )

            create_plan = (
                await execute_warehouse_transfer_reconciliation(
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

            print(
                "D2 PRODUCTION TRANSFER CREATE "
                "= PASS"
            )

            # ==================================================
            # F. IMMUTABLE TRANSFER HEADER + LINE
            # ==================================================

            event_rows = await rows(
                db,
                """
                SELECT
                    id,
                    history_key,
                    source_warehouse_id,
                    destination_warehouse_id,
                    transfer_date,
                    reversal_of_id
                FROM warehouse_transfer_events
                WHERE company_id = :company_id
                  AND history_key = :history_key
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "history_key": history_key,
                },
            )

            assert len(event_rows) == 1

            original_event = event_rows[0]

            assert (
                original_event["reversal_of_id"]
                is None
            )

            assert (
                int(
                    original_event[
                        "source_warehouse_id"
                    ]
                )
                == source_warehouse_id
            )

            assert (
                int(
                    original_event[
                        "destination_warehouse_id"
                    ]
                )
                == destination_warehouse_id
            )

            assert (
                original_event["transfer_date"]
                == d2
            )

            original_event_id = int(
                original_event["id"]
            )

            transfer_lines = await rows(
                db,
                """
                SELECT
                    id,
                    product_id,
                    quantity,
                    issue_document_id,
                    issue_document_line_id,
                    receipt_document_id,
                    source_warehouse_id,
                    destination_warehouse_id
                FROM warehouse_transfer_lines
                WHERE company_id = :company_id
                  AND transfer_event_id = :event_id
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "event_id": original_event_id,
                },
            )

            assert len(transfer_lines) == 1

            transfer_line = transfer_lines[0]

            assert (
                int(
                    transfer_line["product_id"]
                )
                == product_id
            )

            assert (
                Decimal(
                    transfer_line["quantity"]
                )
                == TRANSFER_QTY
            )

            issue_document_id = int(
                transfer_line[
                    "issue_document_id"
                ]
            )

            issue_document_line_id = int(
                transfer_line[
                    "issue_document_line_id"
                ]
            )

            transfer_receipt_document_id = int(
                transfer_line[
                    "receipt_document_id"
                ]
            )

            assert (
                issue_document_id
                != transfer_receipt_document_id
            )

            print(
                "TRANSFER HEADER + BUSINESS LINE "
                "= PASS"
            )

            # ==================================================
            # G. PHYSICAL DOCUMENTS
            # ==================================================

            physical_documents = await rows(
                db,
                """
                SELECT
                    id,
                    document_type::text AS document_type,
                    status::text AS status,
                    document_date,
                    accounting_rule_id
                FROM documents
                WHERE company_id = :company_id
                  AND id IN (
                      :issue_document_id,
                      :receipt_document_id
                  )
                ORDER BY id
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

            assert len(physical_documents) == 2

            docs_by_id = {
                int(row["id"]): row
                for row in physical_documents
            }

            issue_doc = docs_by_id[
                issue_document_id
            ]

            transfer_receipt_doc = docs_by_id[
                transfer_receipt_document_id
            ]

            assert (
                str(
                    issue_doc["document_type"]
                ).lower()
                == "issue"
            )

            assert (
                str(
                    transfer_receipt_doc[
                        "document_type"
                    ]
                ).lower()
                == "receipt"
            )

            assert (
                str(
                    issue_doc["status"]
                ).lower()
                == "posted"
            )

            assert (
                str(
                    transfer_receipt_doc[
                        "status"
                    ]
                ).lower()
                == "posted"
            )

            assert (
                issue_doc["accounting_rule_id"]
                is None
            )

            assert (
                transfer_receipt_doc[
                    "accounting_rule_id"
                ]
                is None
            )

            print(
                "TRANSFER DOCUMENTS "
                "ISSUE + RECEIPT POSTED "
                "= PASS"
            )

            # ==================================================
            # H. SOURCE ICE + FIFO CONSUMPTIONS
            # ==================================================

            source_ice_rows = await rows(
                db,
                """
                SELECT
                    id,
                    valuation_method::text
                        AS valuation_method,
                    quantity,
                    unit_cost,
                    valuation_amount,
                    cost_amount
                FROM inventory_cost_entries
                WHERE company_id = :company_id
                  AND document_id = :document_id
                  AND document_line_id = :line_id
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "document_id": (
                        issue_document_id
                    ),
                    "line_id": (
                        issue_document_line_id
                    ),
                },
            )

            assert len(source_ice_rows) == 1

            source_ice = source_ice_rows[0]

            assert (
                str(
                    source_ice[
                        "valuation_method"
                    ]
                ).lower()
                == "fifo"
            )

            assert (
                Decimal(
                    source_ice["quantity"]
                )
                == TRANSFER_QTY
            )

            assert (
                Decimal(
                    source_ice[
                        "valuation_amount"
                    ]
                )
                == expected_value
            )

            source_ice_id = int(
                source_ice["id"]
            )

            consumptions = await rows(
                db,
                """
                SELECT
                    id,
                    stock_lot_id,
                    quantity,
                    unit_cost
                FROM stock_lot_consumptions
                WHERE company_id = :company_id
                  AND issue_document_id = :document_id
                  AND issue_document_line_id = :line_id
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "document_id": (
                        issue_document_id
                    ),
                    "line_id": (
                        issue_document_line_id
                    ),
                },
            )

            assert (
                len(consumptions)
                == len(expected_slices)
            )

            for actual, expected in zip(
                consumptions,
                expected_slices,
                strict=True,
            ):
                assert (
                    int(
                        actual[
                            "stock_lot_id"
                        ]
                    )
                    == expected[
                        "stock_lot_id"
                    ]
                )

                assert (
                    Decimal(
                        actual["quantity"]
                    )
                    == expected["quantity"]
                )

                assert (
                    Decimal(
                        actual["unit_cost"]
                    )
                    == expected["unit_cost"]
                )

            print(
                "SOURCE FIFO CONSUMPTIONS "
                f"layers={len(consumptions)} "
                "= EXACT PASS"
            )

            # ==================================================
            # I. SOURCE LOT QUANTITIES AFTER ISSUE
            # ==================================================

            source_lots_after_transfer = await rows(
                db,
                """
                SELECT
                    id,
                    remaining_quantity,
                    original_quantity,
                    unit_cost
                FROM stock_lots
                WHERE company_id = :company_id
                  AND id = ANY(:lot_ids)
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "lot_ids": list(
                        source_snapshot_by_id
                    ),
                },
            )

            consumed_by_lot = {
                row["stock_lot_id"]:
                    row["quantity"]
                for row in expected_slices
            }

            for row in source_lots_after_transfer:
                lot_id = int(row["id"])

                before = (
                    source_snapshot_by_id[
                        lot_id
                    ][
                        "remaining_quantity"
                    ]
                )

                consumed = (
                    consumed_by_lot.get(
                        lot_id,
                        Decimal("0"),
                    )
                )

                assert (
                    Decimal(
                        row[
                            "remaining_quantity"
                        ]
                    )
                    == before - consumed
                )

            print(
                "SOURCE LOT DEPLETION "
                "= EXACT PASS"
            )

            # ==================================================
            # J. IMMUTABLE WTVL PROVENANCE
            # ==================================================

            valuation_layers = await rows(
                db,
                """
                SELECT
                    id,
                    transfer_line_id,
                    product_id,
                    destination_warehouse_id,
                    valuation_method::text
                        AS valuation_method,
                    quantity,
                    unit_cost,
                    valuation_amount,
                    source_inventory_cost_entry_id,
                    source_stock_lot_consumption_id,
                    destination_receipt_document_id,
                    destination_receipt_document_line_id
                FROM warehouse_transfer_valuation_layers
                WHERE company_id = :company_id
                  AND transfer_line_id = :transfer_line_id
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "transfer_line_id": int(
                        transfer_line["id"]
                    ),
                },
            )

            assert (
                len(valuation_layers)
                == len(consumptions)
            )

            consumptions_by_id = {
                int(row["id"]): row
                for row in consumptions
            }

            layer_total_quantity = Decimal(
                "0"
            )

            layer_total_value = Decimal(
                "0"
            )

            destination_line_ids = set()

            for layer in valuation_layers:
                assert (
                    str(
                        layer[
                            "valuation_method"
                        ]
                    ).lower()
                    == "fifo"
                )

                assert (
                    int(
                        layer[
                            "source_inventory_cost_entry_id"
                        ]
                    )
                    == source_ice_id
                )

                consumption_id = int(
                    layer[
                        "source_stock_lot_consumption_id"
                    ]
                )

                consumption = (
                    consumptions_by_id[
                        consumption_id
                    ]
                )

                layer_qty = Decimal(
                    layer["quantity"]
                )

                layer_cost = Decimal(
                    layer["unit_cost"]
                )

                layer_value = Decimal(
                    layer[
                        "valuation_amount"
                    ]
                )

                assert (
                    layer_qty
                    == Decimal(
                        consumption[
                            "quantity"
                        ]
                    )
                )

                assert (
                    layer_cost
                    == Decimal(
                        consumption[
                            "unit_cost"
                        ]
                    )
                )

                assert (
                    layer_value
                    == (
                        layer_qty
                        * layer_cost
                    )
                )

                assert (
                    int(
                        layer[
                            "destination_receipt_document_id"
                        ]
                    )
                    == transfer_receipt_document_id
                )

                destination_line_id = int(
                    layer[
                        "destination_receipt_document_line_id"
                    ]
                )

                assert (
                    destination_line_id
                    not in destination_line_ids
                )

                destination_line_ids.add(
                    destination_line_id
                )

                layer_total_quantity += (
                    layer_qty
                )

                layer_total_value += (
                    layer_value
                )

            assert (
                layer_total_quantity
                == TRANSFER_QTY
            )

            assert (
                layer_total_value
                == expected_value
            )

            print(
                "WTVL SOURCE PROVENANCE "
                "QUANTITY + VALUE CONSERVATION "
                "= EXACT PASS"
            )

            # ==================================================
            # K. DESTINATION RECEIPT LINES + FIFO LOTS
            # ==================================================

            destination_lines = await rows(
                db,
                """
                SELECT
                    id,
                    product_id,
                    warehouse_id,
                    quantity,
                    price
                FROM document_lines
                WHERE document_id = :document_id
                ORDER BY id
                """,
                {
                    "document_id": (
                        transfer_receipt_document_id
                    ),
                },
            )

            assert (
                len(destination_lines)
                == len(valuation_layers)
            )

            destination_lines_by_id = {
                int(row["id"]): row
                for row in destination_lines
            }

            destination_transfer_lots = await rows(
                db,
                """
                SELECT
                    id,
                    source_document_id,
                    source_document_line_id,
                    received_date,
                    original_quantity,
                    remaining_quantity,
                    unit_cost
                FROM stock_lots
                WHERE company_id = :company_id
                  AND product_id = :product_id
                  AND warehouse_id = :warehouse_id
                  AND source_document_id = :document_id
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "product_id": product_id,
                    "warehouse_id": (
                        destination_warehouse_id
                    ),
                    "document_id": (
                        transfer_receipt_document_id
                    ),
                },
            )

            assert (
                len(destination_transfer_lots)
                == len(valuation_layers)
            )

            destination_lots_by_line = {
                int(
                    row[
                        "source_document_line_id"
                    ]
                ): row
                for row
                in destination_transfer_lots
            }

            assert (
                set(
                    destination_lots_by_line
                )
                == destination_line_ids
            )

            for layer in valuation_layers:
                line_id = int(
                    layer[
                        "destination_receipt_document_line_id"
                    ]
                )

                line = (
                    destination_lines_by_id[
                        line_id
                    ]
                )

                lot = (
                    destination_lots_by_line[
                        line_id
                    ]
                )

                layer_qty = Decimal(
                    layer["quantity"]
                )

                layer_cost = Decimal(
                    layer["unit_cost"]
                )

                assert (
                    int(
                        line[
                            "warehouse_id"
                        ]
                    )
                    == destination_warehouse_id
                )

                assert (
                    Decimal(
                        line["quantity"]
                    )
                    == layer_qty
                )

                assert (
                    Decimal(
                        line["price"]
                    )
                    == layer_cost
                )

                assert (
                    Decimal(
                        lot[
                            "original_quantity"
                        ]
                    )
                    == layer_qty
                )

                assert (
                    Decimal(
                        lot[
                            "remaining_quantity"
                        ]
                    )
                    == layer_qty
                )

                assert (
                    Decimal(
                        lot["unit_cost"]
                    )
                    == layer_cost
                )

            new_destination_ids = {
                int(row["id"])
                for row
                in destination_transfer_lots
            }

            assert not (
                new_destination_ids
                & destination_ids_before
            )

            print(
                "DESTINATION FIFO LAYERS "
                f"count={len(destination_transfer_lots)} "
                "= EXACT HISTORICAL PASS"
            )

            # ==================================================
            # L. IMMUTABLE TRUTH SNAPSHOT BEFORE REVERSAL
            # ==================================================

            consumption_truth = tuple(
                (
                    int(row["id"]),
                    int(row["stock_lot_id"]),
                    Decimal(row["quantity"]),
                    Decimal(row["unit_cost"]),
                )
                for row in consumptions
            )

            layer_truth = tuple(
                (
                    int(row["id"]),
                    int(
                        row[
                            "source_inventory_cost_entry_id"
                        ]
                    ),
                    int(
                        row[
                            "source_stock_lot_consumption_id"
                        ]
                    ),
                    Decimal(row["quantity"]),
                    Decimal(row["unit_cost"]),
                    Decimal(
                        row[
                            "valuation_amount"
                        ]
                    ),
                    int(
                        row[
                            "destination_receipt_document_line_id"
                        ]
                    ),
                )
                for row
                in valuation_layers
            )

            ice_truth = (
                int(source_ice["id"]),
                Decimal(
                    source_ice["quantity"]
                ),
                Decimal(
                    source_ice[
                        "unit_cost"
                    ]
                ),
                Decimal(
                    source_ice[
                        "valuation_amount"
                    ]
                ),
                Decimal(
                    source_ice[
                        "cost_amount"
                    ]
                ),
            )

            # ==================================================
            # M. D3 REAL REVERSAL THROUGH PRODUCTION EXECUTOR
            # ==================================================

            reverse_plan = (
                await execute_warehouse_transfer_reconciliation(
                    db,
                    company_id=COMPANY_ID,
                    history_key=history_key,
                    target=None,
                    adjustment_date=d3,
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert (
                reverse_plan.action.value
                == "reverse"
            )

            print(
                "D3 PRODUCTION TRANSFER REVERSAL "
                "= PASS"
            )

            # ==================================================
            # N. REVERSAL HISTORY IS APPEND-ONLY
            # ==================================================

            history_after = await rows(
                db,
                """
                SELECT
                    id,
                    transfer_date,
                    reversal_of_id,
                    source_warehouse_id,
                    destination_warehouse_id
                FROM warehouse_transfer_events
                WHERE company_id = :company_id
                  AND history_key = :history_key
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "history_key": history_key,
                },
            )

            assert len(history_after) == 2

            assert (
                int(history_after[0]["id"])
                == original_event_id
            )

            assert (
                history_after[0][
                    "reversal_of_id"
                ]
                is None
            )

            reversal_event = history_after[1]

            assert (
                int(
                    reversal_event[
                        "reversal_of_id"
                    ]
                )
                == original_event_id
            )

            assert (
                reversal_event[
                    "transfer_date"
                ]
                == d3
            )

            print(
                "IMMUTABLE REVERSAL EVENT "
                "= PASS"
            )

            # ==================================================
            # O. DESTINATION MUST BE ZEROED
            # ==================================================

            destination_after_reversal = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        source_document_line_id,
                        original_quantity,
                        remaining_quantity,
                        unit_cost
                    FROM stock_lots
                    WHERE company_id = :company_id
                      AND source_document_id = :document_id
                    ORDER BY id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "document_id": (
                            transfer_receipt_document_id
                        ),
                    },
                )
            )

            assert (
                len(
                    destination_after_reversal
                )
                == len(
                    destination_transfer_lots
                )
            )

            for row in (
                destination_after_reversal
            ):
                assert (
                    Decimal(
                        row[
                            "remaining_quantity"
                        ]
                    )
                    == Decimal("0")
                )

            print(
                "DESTINATION RECEIPT FIFO REVERSAL "
                "= ZERO PASS"
            )

            # ==================================================
            # P. SOURCE EXACT LOTS MUST BE RESTORED
            # ==================================================

            source_after_reversal = await rows(
                db,
                """
                SELECT
                    id,
                    original_quantity,
                    remaining_quantity,
                    unit_cost
                FROM stock_lots
                WHERE company_id = :company_id
                  AND id = ANY(:lot_ids)
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "lot_ids": list(
                        source_snapshot_by_id
                    ),
                },
            )

            assert (
                len(source_after_reversal)
                == len(
                    source_snapshot_by_id
                )
            )

            for row in source_after_reversal:
                lot_id = int(row["id"])

                before = (
                    source_snapshot_by_id[
                        lot_id
                    ]
                )

                assert (
                    Decimal(
                        row[
                            "remaining_quantity"
                        ]
                    )
                    == before[
                        "remaining_quantity"
                    ]
                )

                assert (
                    Decimal(
                        row[
                            "original_quantity"
                        ]
                    )
                    == before[
                        "original_quantity"
                    ]
                )

                assert (
                    Decimal(
                        row["unit_cost"]
                    )
                    == before[
                        "unit_cost"
                    ]
                )

            print(
                "SOURCE FIFO EXACT LOT RESTORATION "
                "= PASS"
            )

            # ==================================================
            # Q. PHYSICAL DOCUMENTS MUST BE REVERSED
            # ==================================================

            statuses_after = await rows(
                db,
                """
                SELECT
                    id,
                    status::text AS status,
                    reversed_at,
                    reversed_by
                FROM documents
                WHERE company_id = :company_id
                  AND id IN (
                      :issue_document_id,
                      :receipt_document_id
                  )
                ORDER BY id
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

            assert len(statuses_after) == 2

            for row in statuses_after:
                assert (
                    str(
                        row["status"]
                    ).lower()
                    == "reversed"
                )

                assert (
                    row["reversed_at"]
                    is not None
                )

                assert (
                    int(
                        row["reversed_by"]
                    )
                    == USER_ID
                )

            print(
                "SOURCE + DESTINATION DOCUMENTS "
                "REVERSED = PASS"
            )

            # ==================================================
            # R. IMMUTABLE COST / PROVENANCE TRUTH
            # ==================================================

            consumptions_after = await rows(
                db,
                """
                SELECT
                    id,
                    stock_lot_id,
                    quantity,
                    unit_cost
                FROM stock_lot_consumptions
                WHERE company_id = :company_id
                  AND issue_document_id = :document_id
                  AND issue_document_line_id = :line_id
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "document_id": (
                        issue_document_id
                    ),
                    "line_id": (
                        issue_document_line_id
                    ),
                },
            )

            consumption_truth_after = tuple(
                (
                    int(row["id"]),
                    int(row["stock_lot_id"]),
                    Decimal(row["quantity"]),
                    Decimal(row["unit_cost"]),
                )
                for row
                in consumptions_after
            )

            assert (
                consumption_truth_after
                == consumption_truth
            )

            layers_after = await rows(
                db,
                """
                SELECT
                    id,
                    source_inventory_cost_entry_id,
                    source_stock_lot_consumption_id,
                    quantity,
                    unit_cost,
                    valuation_amount,
                    destination_receipt_document_line_id
                FROM warehouse_transfer_valuation_layers
                WHERE company_id = :company_id
                  AND transfer_line_id = :transfer_line_id
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "transfer_line_id": int(
                        transfer_line["id"]
                    ),
                },
            )

            layer_truth_after = tuple(
                (
                    int(row["id"]),
                    int(
                        row[
                            "source_inventory_cost_entry_id"
                        ]
                    ),
                    int(
                        row[
                            "source_stock_lot_consumption_id"
                        ]
                    ),
                    Decimal(row["quantity"]),
                    Decimal(row["unit_cost"]),
                    Decimal(
                        row[
                            "valuation_amount"
                        ]
                    ),
                    int(
                        row[
                            "destination_receipt_document_line_id"
                        ]
                    ),
                )
                for row
                in layers_after
            )

            assert (
                layer_truth_after
                == layer_truth
            )

            ice_after_rows = await rows(
                db,
                """
                SELECT
                    id,
                    quantity,
                    unit_cost,
                    valuation_amount,
                    cost_amount
                FROM inventory_cost_entries
                WHERE id = :id
                """,
                {
                    "id": source_ice_id,
                },
            )

            assert len(ice_after_rows) == 1

            ice_after = (
                ice_after_rows[0]
            )

            ice_truth_after = (
                int(ice_after["id"]),
                Decimal(
                    ice_after["quantity"]
                ),
                Decimal(
                    ice_after["unit_cost"]
                ),
                Decimal(
                    ice_after[
                        "valuation_amount"
                    ]
                ),
                Decimal(
                    ice_after[
                        "cost_amount"
                    ]
                ),
            )

            assert (
                ice_truth_after
                == ice_truth
            )

            print(
                "FIFO CONSUMPTION + ICE + WTVL "
                "IMMUTABLE TRUTH = PASS"
            )

            print()
            print(
                "WAREHOUSE FIFO POSTGRESQL "
                "CREATE + REVERSAL CHRONOLOGY = PASS"
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

    # ======================================================
    # S. EXACT OUTER TRANSACTION ROLLBACK PROOF
    # ======================================================

    after = await complete_baseline()

    assert after == baseline, (
        "\nWarehouse Transfer FIFO PostgreSQL "
        "E2E rollback did not restore exact "
        "row-count baseline.\n"
        f"before={baseline}\n"
        f"after={after}"
    )

    print(
        "WAREHOUSE FIFO FULL TRANSACTION "
        "ROLLBACK = PASS"
    )

    if scenario_error is not None:
        raise scenario_error.with_traceback(
            scenario_traceback
        )
