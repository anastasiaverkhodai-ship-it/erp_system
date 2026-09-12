from __future__ import annotations

import importlib.util
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import engine
from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.document_line import DocumentLine
from app.services.reservation_persistence_service import (
    get_reserved_quantity_for_source_line,
    reserve_source_line,
)



COMPANY_ID = 1
USER_ID = 1

PURCHASE_QTY = Decimal(
    "120.0000"
)

ROUTED_QTY = Decimal(
    "50.0000"
)

DOWNSTREAM_ISSUE_QTY = Decimal(
    "20.0000"
)

DESTINATION_ON_HAND_QTY = Decimal(
    "30.0000"
)


def load_module(
    name: str,
    path: str,
):
    file_path = Path(
        path
    )

    spec = (
        importlib.util
        .spec_from_file_location(
            name,
            file_path,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            f"Cannot load {path}"
        )

    module = (
        importlib.util
        .module_from_spec(
            spec
        )
    )

    sys.modules[
        name
    ] = module

    spec.loader.exec_module(
        module
    )

    return module


fifo = load_module(
    "_mixed_fifo_pvc_pg",
    "tests/"
    "test_purchase_value_correction_fifo_impact_"
    "postgresql_chronology.py",
)

wt = load_module(
    "_mixed_fifo_transfer_pg",
    "tests/"
    "test_warehouse_transfer_fifo_postgresql_chronology.py",
)

on_hand = load_module(
    "_mixed_fifo_transfer_on_hand_pg",
    "tests/"
    "test_purchase_value_correction_fifo_transfer_"
    "postgresql_chronology.py",
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

    return [
        row._mapping
        for row in result
    ]


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


async def create_destination_fifo_issue(
    db: AsyncSession,
    *,
    fixture,
    product_id: int,
    warehouse_id: int,
    quantity: Decimal,
    document_date,
):
    """
    Create a real downstream FIFO issue by reusing the same
    production sales-order fulfillment path already proven by
    the existing FIFO PVC PostgreSQL chronology test.

    The local fixture copy redirects only this sales issue to
    the transfer destination warehouse and D3 date.
    """

    assert int(
        fixture[
            "product_id"
        ]
    ) == product_id

    issue_fixture = dict(
        fixture
    )

    issue_fixture[
        "warehouse_id"
    ] = warehouse_id

    issue_fixture[
        "business_date"
    ] = document_date

    issue_fixture[
        "suffix"
    ] = (
        fixture[
            "suffix"
        ]
        + "-WTMIX-"
        + uuid4().hex[
            :8
        ]
    )

    (
        sales_order_id,
        sales_line_id,
    ) = await fifo.create_sales_order(
        db,
        fixture=issue_fixture,
        quantity=quantity,
    )

    await reserve_source_line(
        db,
        company_id=COMPANY_ID,
        source_document_id=sales_order_id,
        source_document_line_id=sales_line_id,
        quantity=quantity,
    )

    reserved_before = await get_reserved_quantity_for_source_line(
        db,
        company_id=COMPANY_ID,
        source_document_id=sales_order_id,
        source_document_line_id=sales_line_id,
    )

    assert Decimal(
        reserved_before
    ) == quantity

    issue_rule_id = (
        await fifo.find_or_seed_issue_accounting_rule(
            db
        )
    )

    issue = (
        await fifo.execute_sales_order_fulfillment(
            db,
            company_id=COMPANY_ID,
            trade_document_id=sales_order_id,
            warehouse_document_number=(
                "PVC-WT-FIFO-MIXED-I-"
                + uuid4().hex[
                    :12
                ]
            ),
            document_date=document_date,
            accounting_rule_id=issue_rule_id,
            created_by=USER_ID,
            request_lines=(
                fifo.SalesOrderFulfillmentRequestLine(
                    trade_document_line_id=(
                        sales_line_id
                    ),
                    quantity=quantity,
                ),
            ),
        )
    )

    await db.flush()

    reserved_after = await get_reserved_quantity_for_source_line(
        db,
        company_id=COMPANY_ID,
        source_document_id=sales_order_id,
        source_document_line_id=sales_line_id,
    )

    assert Decimal(
        reserved_after
    ) == Decimal(
        "0.0000"
    )

    issue_document_id = int(
        issue
        .fulfillment
        .warehouse_document_id
    )

    issue_document = (
        await db.get(
            Document,
            issue_document_id,
        )
    )

    assert issue_document is not None

    issue_line_rows = await rows(
        db,
        """
        SELECT
            id,
            product_id,
            warehouse_id,
            quantity
        FROM document_lines
        WHERE document_id =
              :document_id
          AND product_id =
              :product_id
          AND warehouse_id =
              :warehouse_id
        ORDER BY id
        """,
        {
            "document_id": (
                issue_document_id
            ),
            "product_id": (
                product_id
            ),
            "warehouse_id": (
                warehouse_id
            ),
        },
    )

    assert len(
        issue_line_rows
    ) == 1

    issue_line = (
        await db.get(
            DocumentLine,
            int(
                issue_line_rows[
                    0
                ][
                    "id"
                ]
            ),
        )
    )

    assert issue_line is not None

    assert Decimal(
        issue_line.quantity
    ) == quantity

    assert int(
        issue_line.warehouse_id
    ) == warehouse_id

    return (
        issue_document,
        issue_line,
    )



@pytest.mark.asyncio
async def test_fifo_transfer_pvc_mixed_postgresql_chronology():
    """
    D1:
        purchase receipt 120

    D2:
        transfer 50 of the purchase lot to destination

    D3:
        downstream destination ISSUE 20

    D4:
        PVC 120 -> 108

    Expected active PVC destinations:

        source on_hand:
            qty 70
            70 -> 63

        destination issued:
            qty 20
            20 -> 18

        destination on_hand:
            qty 30
            30 -> 27

    Conservation:
        qty:
            70 + 20 + 30 = 120

        original:
            70 + 20 + 30 = 120

        corrected:
            63 + 18 + 27 = 108
    """

    await engine.dispose(
        close=False
    )

    baseline = (
        await on_hand.transfer_pvc_baseline()
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
            # ---------------------------------------------
            # A. Business fixture
            # ---------------------------------------------

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

            d3 = (
                d1
                + timedelta(
                    days=2
                )
            )

            d4 = (
                d1
                + timedelta(
                    days=3
                )
            )

            period_end = fixture.get(
                "period_end"
            )

            if (
                period_end is not None
                and d4 > period_end
            ):
                pytest.skip(
                    "FIFO mixed PostgreSQL chronology "
                    "requires four usable business dates "
                    "inside the open period"
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

            method = await scalar(
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
                method
            ) == "fifo"

            print(
                "FIFO MIXED COMPANY "
                f"source={source_warehouse_id} "
                f"destination={destination_warehouse_id} "
                "= PASS"
            )

            # ---------------------------------------------
            # B. Pre-existing source FIFO
            # ---------------------------------------------

            old_fifo_quantity = (
                await fifo.preexisting_fifo_quantity(
                    db,
                    product_id=product_id,
                    warehouse_id=source_warehouse_id,
                )
            )

            transfer_quantity = (
                old_fifo_quantity
                + ROUTED_QTY
            )

            destination_fifo_before_transfer = (
                await fifo.preexisting_fifo_quantity(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        destination_warehouse_id
                    ),
                )
            )

            # ---------------------------------------------
            # C. D1 purchase receipt 120
            # ---------------------------------------------

            receipt = (
                await fifo.execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=fixture[
                        "order_id"
                    ],
                    warehouse_document_number=(
                        "PVC-WT-FIFO-MIX-R-"
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
                        fifo.PurchaseOrderFulfillmentRequestLine(
                            trade_document_line_id=fixture[
                                "order_line_id"
                            ],
                            quantity=PURCHASE_QTY,
                        ),
                    ),
                )
            )

            await db.flush()

            fulfillment_line_id = (
                await fifo.base.fulfillment_line_id(
                    db,
                    fulfillment_id=(
                        receipt.fulfillment.id
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
                    "remaining_quantity"
                ]
            ) == PURCHASE_QTY

            allocation = (
                await fifo.create_invoice_fulfillment_allocation(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=fixture[
                        "invoice_id"
                    ],
                    invoice_line_id=fixture[
                        "invoice_line_id"
                    ],
                    fulfillment_id=(
                        receipt.fulfillment.id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    quantity=PURCHASE_QTY,
                    created_by=USER_ID,
                )
            )

            await db.flush()

            # ---------------------------------------------
            # D. D2 transfer exact 50 from new lot
            # ---------------------------------------------

            history_key = str(
                uuid4()
            )

            transfer_target = (
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

            await wt.execute_warehouse_transfer_reconciliation(
                db,
                company_id=COMPANY_ID,
                history_key=history_key,
                target=transfer_target,
                adjustment_date=None,
                created_by=USER_ID,
            )

            await db.flush()

            routed_layers = await rows(
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
                  ON slc.company_id = wtvl.company_id
                 AND slc.id =
                     wtvl.source_stock_lot_consumption_id
                WHERE wtvl.company_id = :company_id
                  AND slc.stock_lot_id = :source_lot_id
                ORDER BY wtvl.id
                """,
                {
                    "company_id": COMPANY_ID,
                    "source_lot_id": (
                        source_lot_id
                    ),
                },
            )

            assert len(
                routed_layers
            ) == 1

            route = routed_layers[
                0
            ]

            assert Decimal(
                route[
                    "quantity"
                ]
            ) == ROUTED_QTY

            destination_lots = await rows(
                db,
                """
                SELECT
                    id,
                    company_id,
                    product_id,
                    warehouse_id,
                    source_document_id,
                    source_document_line_id,
                    original_quantity,
                    remaining_quantity,
                    unit_cost
                FROM stock_lots
                WHERE company_id = :company_id
                  AND source_document_id =
                      :document_id
                  AND source_document_line_id =
                      :line_id
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "document_id": (
                        int(
                            route[
                                "destination_receipt_document_id"
                            ]
                        )
                    ),
                    "line_id": (
                        int(
                            route[
                                "destination_receipt_document_line_id"
                            ]
                        )
                    ),
                },
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

            print(
                "D2 TRANSFER 50 TO DESTINATION = PASS"
            )

            # ---------------------------------------------
            # E. D3 real downstream destination ISSUE = 20
            # ---------------------------------------------

            destination_fifo_prefix = (
                destination_fifo_before_transfer
                + old_fifo_quantity
            )

            downstream_physical_issue_quantity = (
                destination_fifo_prefix
                + DOWNSTREAM_ISSUE_QTY
            )

            issue_document, issue_line = (
                await create_destination_fifo_issue(
                    db,
                    fixture=fixture,
                    product_id=product_id,
                    warehouse_id=(
                        destination_warehouse_id
                    ),
                    quantity=(
                        downstream_physical_issue_quantity
                    ),
                    document_date=d3,
                )
            )

            await db.flush()

            destination_after_issue = await rows(
                db,
                """
                SELECT
                    id,
                    original_quantity,
                    remaining_quantity
                FROM stock_lots
                WHERE company_id = :company_id
                  AND id = :lot_id
                """,
                {
                    "company_id": COMPANY_ID,
                    "lot_id": (
                        destination_lot_id
                    ),
                },
            )

            assert len(
                destination_after_issue
            ) == 1

            assert Decimal(
                destination_after_issue[
                    0
                ][
                    "remaining_quantity"
                ]
            ) == DESTINATION_ON_HAND_QTY

            all_downstream_consumptions = await rows(
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
                  AND issue_document_id = :issue_document_id
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "issue_document_id": (
                        issue_document.id
                    ),
                },
            )

            total_physical_issue_consumption = sum(
                (
                    Decimal(
                        row[
                            "quantity"
                        ]
                    )
                    for row
                    in all_downstream_consumptions
                ),
                Decimal(
                    "0"
                ),
            )

            assert (
                total_physical_issue_consumption
                == downstream_physical_issue_quantity
            )

            assert (
                downstream_physical_issue_quantity
                - destination_fifo_prefix
                == DOWNSTREAM_ISSUE_QTY
            )

            print(
                "D3 FIFO PREFIX + TARGET SLICE: "
                f"physical={downstream_physical_issue_quantity} "
                f"prefix={destination_fifo_prefix} "
                f"target={DOWNSTREAM_ISSUE_QTY} = PASS"
            )

            downstream_consumptions = await rows(
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
                  AND stock_lot_id = :lot_id
                  AND issue_document_id = :issue_document_id
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "lot_id": (
                        destination_lot_id
                    ),
                    "issue_document_id": (
                        issue_document.id
                    ),
                },
            )

            assert len(
                downstream_consumptions
            ) == 1

            downstream = (
                downstream_consumptions[
                    0
                ]
            )

            assert Decimal(
                downstream[
                    "quantity"
                ]
            ) == DOWNSTREAM_ISSUE_QTY

            downstream_consumption_id = int(
                downstream[
                    "id"
                ]
            )

            print(
                "D3 DESTINATION DOWNSTREAM ISSUE 20 = PASS"
            )

            # ---------------------------------------------
            # F. D4 PVC 120 -> 108
            # ---------------------------------------------

            correction = (
                await fifo.base.insert_value_correction(
                    db,
                    invoice_id=fixture[
                        "invoice_id"
                    ],
                    invoice_line_id=fixture[
                        "invoice_line_id"
                    ],
                    product_id=product_id,
                    correction_date=d4,
                    original_gross_amount=(
                        "120.00"
                    ),
                    corrected_gross_amount=(
                        "108.00"
                    ),
                    reason_code=(
                        "postgres_fifo_transfer_mixed"
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
                    adjustment_date=d4,
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

            impact_result = (
                await fifo.reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d4,
                    created_by=USER_ID,
                )
            )

            assert len(
                impact_result.created_events
            ) == 3

            history = (
                await fifo.impact_history(
                    db,
                    allocation_event_id=(
                        pvc_allocation.id
                    ),
                )
            )

            active = (
                fifo.active_impact_rows(
                    history
                )
            )

            assert len(
                active
            ) == 3

            source_rows = [
                row
                for row in active
                if int(
                    row[
                        "stock_lot_id"
                    ]
                ) == source_lot_id
            ]

            destination_rows = [
                row
                for row in active
                if int(
                    row[
                        "stock_lot_id"
                    ]
                ) == destination_lot_id
            ]

            assert len(
                source_rows
            ) == 1

            assert len(
                destination_rows
            ) == 2

            source_impact = (
                source_rows[
                    0
                ]
            )

            destination_issued = next(
                row
                for row in destination_rows
                if row[
                    "destination_kind"
                ] == "issued"
            )

            destination_on_hand = next(
                row
                for row in destination_rows
                if row[
                    "destination_kind"
                ] == "on_hand"
            )

            # Source 70
            assert Decimal(
                source_impact[
                    "quantity"
                ]
            ) == Decimal(
                "70.0000"
            )

            assert (
                source_impact[
                    "destination_kind"
                ]
                == "on_hand"
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

            # Destination issued 20
            assert Decimal(
                destination_issued[
                    "quantity"
                ]
            ) == DOWNSTREAM_ISSUE_QTY

            assert int(
                destination_issued[
                    "stock_lot_consumption_id"
                ]
            ) == downstream_consumption_id

            assert int(
                destination_issued[
                    "issue_document_id"
                ]
            ) == issue_document.id

            assert int(
                destination_issued[
                    "issue_document_line_id"
                ]
            ) == issue_line.id

            assert (
                destination_issued[
                    "recognition_date"
                ]
                == d4
            )

            fifo.assert_money(
                destination_issued[
                    "original_base_amount"
                ],
                "20.00",
            )

            fifo.assert_money(
                destination_issued[
                    "corrected_base_amount"
                ],
                "18.00",
            )

            # Destination on hand 30
            assert Decimal(
                destination_on_hand[
                    "quantity"
                ]
            ) == DESTINATION_ON_HAND_QTY

            assert (
                destination_on_hand[
                    "stock_lot_consumption_id"
                ]
                is None
            )

            fifo.assert_money(
                destination_on_hand[
                    "original_base_amount"
                ],
                "30.00",
            )

            fifo.assert_money(
                destination_on_hand[
                    "corrected_base_amount"
                ],
                "27.00",
            )

            # ---------------------------------------------
            # G. Conservation
            # ---------------------------------------------

            total_quantity = sum(
                (
                    Decimal(
                        row[
                            "quantity"
                        ]
                    )
                    for row in active
                ),
                Decimal(
                    "0"
                ),
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
                Decimal(
                    "0"
                ),
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
                Decimal(
                    "0"
                ),
            )

            assert (
                total_quantity
                == PURCHASE_QTY
            )

            assert (
                total_original
                == Decimal(
                    "120.00"
                )
            )

            assert (
                total_corrected
                == Decimal(
                    "108.00"
                )
            )

            print(
                "FIFO MIXED PVC = "
                "70 SOURCE ON_HAND + "
                "20 DESTINATION ISSUED + "
                "30 DESTINATION ON_HAND = PASS"
            )

            print(
                "FIFO MIXED MONEY "
                "63 + 18 + 27 = 108 PASS"
            )

            # ---------------------------------------------
            # H. Idempotency
            # ---------------------------------------------

            repeat = (
                await fifo.reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d4,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat.created_events
                == ()
            )

            print(
                "FIFO MIXED IDEMPOTENCY = PASS"
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
        await on_hand.transfer_pvc_baseline()
    )

    assert (
        after
        == baseline
    )

    print(
        "FIFO MIXED OUTER ROLLBACK = PASS"
    )
