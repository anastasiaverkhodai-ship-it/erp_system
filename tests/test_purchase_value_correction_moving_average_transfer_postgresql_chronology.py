import importlib.util
import os
from pathlib import Path
import sys
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5
from decimal import Decimal

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

Q8 = Decimal("0.00000001")

PURCHASE_QTY = Decimal("120.0000")
TRANSFER_QTY = Decimal("50.0000")

ORIGINAL_VALUE = Decimal("120.00000000")
CORRECTED_VALUE = Decimal("108.00000000")
PVC_DELTA = Decimal("-12.00000000")

EXPECTED_SOURCE_QTY = Decimal("70.0000")
EXPECTED_DESTINATION_QTY = Decimal("50.0000")

EXPECTED_SOURCE_ORIGINAL_VALUE = Decimal("70.00000000")
EXPECTED_SOURCE_CORRECTED_VALUE = Decimal("63.00000000")

EXPECTED_DESTINATION_ORIGINAL_VALUE = Decimal("50.00000000")
EXPECTED_DESTINATION_CORRECTED_VALUE = Decimal("45.00000000")


pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run real PostgreSQL MA transfer PVC chronology"
    ),
)


def _load_module(
    *,
    filename: str,
    module_name: str,
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


ma = _load_module(
    filename=(
        "test_purchase_value_correction_"
        "moving_average_gl_postgresql_chronology.py"
    ),
    module_name=(
        "_ma_transfer_pvc_pg_base"
    ),
)

fifo = ma.base
base = fifo.base


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


async def scalar_or_none(
    db,
    sql,
    params=None,
):
    return (
        await db.execute(
            text(sql),
            params or {},
        )
    ).scalar_one_or_none()


async def rows(
    db,
    sql,
    params=None,
):
    return (
        await db.execute(
            text(sql),
            params or {},
        )
    ).mappings().all()


async def mapping_one(
    db,
    sql,
    params=None,
):
    return (
        await db.execute(
            text(sql),
            params or {},
        )
    ).mappings().one()


def q8(
    value,
):
    return Decimal(
        value
    ).quantize(
        Q8
    )


async def complete_baseline():
    """
    Reuse proven MA/PVC full baseline and add transfer tables.
    """

    result = dict(
        await ma.complete_table_counts()
    )

    async with engine.connect() as connection:
        for table in (
            "warehouse_transfer_events",
            "warehouse_transfer_lines",
            "warehouse_transfer_valuation_layers",
        ):
            result[
                table
            ] = int(
                (
                    await connection.execute(
                        text(
                            f"SELECT COUNT(*) FROM {table}"
                        )
                    )
                ).scalar_one()
            )

    return result


async def ma_balance(
    db,
    *,
    product_id,
    warehouse_id,
):
    return await ma.ma_balance(
        db,
        product_id=product_id,
        warehouse_id=warehouse_id,
    )


async def active_replay_events(
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
            product_id,
            warehouse_id,
            effect_kind,
            source_moving_average_movement_id,
            source_inventory_cost_entry_id,
            recognition_date,
            quantity,
            original_valuation_amount,
            corrected_valuation_amount,
            reversal_of_id
        FROM purchase_value_correction_ma_replay_events e
        WHERE e.company_id = :company_id
          AND e.purchase_value_correction_allocation_event_id
              = :allocation_event_id
          AND e.reversal_of_id IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM purchase_value_correction_ma_replay_events r
              WHERE r.company_id = e.company_id
                AND r.reversal_of_id = e.id
          )
        ORDER BY
            warehouse_id,
            effect_kind,
            id
        """,
        {
            "company_id":
                COMPANY_ID,
            "allocation_event_id":
                allocation_event_id,
        },
    )


@pytest.mark.asyncio
async def test_purchase_value_correction_moving_average_transfer_postgresql_on_hand():
    """
    REAL PostgreSQL chronology:

        D1 purchase receipt 120 @ 1
            -> source MA receipt / balance
            -> exact IFA/PVCA ownership

        D2 warehouse transfer 50
            -> source MA ISSUE ICE
            -> exact WTVL source ICE provenance
            -> destination transfer receipt ICE
            -> destination MA receipt/balance
            -> zero transfer JournalEntry

        D3 PVC 120 -> 108
            -> production allocation reconciliation
            -> production MA peer reconciliation
            -> source on_hand 70: 70 -> 63
            -> destination on_hand 50: 50 -> 45

    Critical:
        source transfer ISSUE is routing boundary, NOT final
        economic PVC issued destination.

    Outer transaction owns rollback.
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
            # A. REAL BUSINESS FIXTURE + MA METHOD
            # ==================================================

            fixture = (
                await base.create_business_fixture(
                    db
                )
            )

            await ma.set_company_to_moving_average(
                db
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
                await base.open_period_end(
                    db,
                    business_date=d1,
                )
            )

            assert period_end is not None

            if d3 > period_end:
                pytest.skip(
                    "Real MA transfer PVC E2E requires "
                    "three usable business dates inside "
                    "the open accounting period"
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
                        "company_id":
                            COMPANY_ID,
                        "source_warehouse_id":
                            source_warehouse_id,
                    },
                )
            )

            assert (
                destination_warehouse_id
                != source_warehouse_id
            )

            product_id = int(
                fixture[
                    "product_id"
                ]
            )

            print(
                "REAL FIXTURE "
                f"product={product_id} "
                f"source={source_warehouse_id} "
                f"destination={destination_warehouse_id}"
            )

            # ==================================================
            # B. D1 REAL PURCHASE RECEIPT 120
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
                        "PVC-MA-WT-R-"
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
                            quantity=PURCHASE_QTY,
                        ),
                    ),
                )
            )

            fulfillment_line_id = int(
                await base.fulfillment_line_id(
                    db,
                    fulfillment_id=(
                        receipt
                        .fulfillment
                        .id
                    ),
                )
            )

            allocation = (
                await ma.create_invoice_fulfillment_allocation(
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
                    quantity=PURCHASE_QTY,
                    created_by=USER_ID,
                )
            )

            assert allocation.id is not None

            source_before_transfer = (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        source_warehouse_id
                    ),
                )
            )

            assert (
                source_before_transfer[
                    "quantity"
                ]
                == PURCHASE_QTY
            )

            assert q8(
                source_before_transfer[
                    "inventory_value"
                ]
            ) == ORIGINAL_VALUE

            destination_balance_before_transfer_id = (
                await scalar_or_none(
                    db,
                    """
                    SELECT id
                    FROM moving_average_balances
                    WHERE company_id = :company_id
                      AND product_id = :product_id
                      AND warehouse_id = :warehouse_id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "product_id":
                            product_id,
                        "warehouse_id":
                            destination_warehouse_id,
                    },
                )
            )

            assert (
                destination_balance_before_transfer_id
                is None
            )

            print(
                "D1 REAL PURCHASE RECEIPT 120 "
                "VALUE 120 = PASS"
            )

            # ==================================================
            # C. D2 REAL WAREHOUSE TRANSFER 50
            # ==================================================

            history_key = str(
                uuid5(
                    NAMESPACE_URL,
                    (
                        "ma-pvc-transfer-on-hand-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                )
            )

            transfer_target = (
                normalize_transfer_target(
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
            )

            transfer_plan = (
                await execute_warehouse_transfer_reconciliation(
                    db,
                    company_id=COMPANY_ID,
                    history_key=history_key,
                    target=transfer_target,
                    adjustment_date=None,
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert (
                transfer_plan.action.value
                == "create"
            )

            transfer_event = await mapping_one(
                db,
                """
                SELECT
                    id,
                    reversal_of_id
                FROM warehouse_transfer_events
                WHERE company_id = :company_id
                  AND history_key = :history_key
                ORDER BY id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "history_key":
                        history_key,
                },
            )

            assert (
                transfer_event[
                    "reversal_of_id"
                ]
                is None
            )

            transfer_line = await mapping_one(
                db,
                """
                SELECT
                    id,
                    issue_document_id,
                    issue_document_line_id,
                    receipt_document_id
                FROM warehouse_transfer_lines
                WHERE company_id = :company_id
                  AND transfer_event_id = :event_id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "event_id":
                        int(
                            transfer_event[
                                "id"
                            ]
                        ),
                },
            )

            source_issue_document_id = int(
                transfer_line[
                    "issue_document_id"
                ]
            )

            source_issue_line_id = int(
                transfer_line[
                    "issue_document_line_id"
                ]
            )

            destination_receipt_document_id = int(
                transfer_line[
                    "receipt_document_id"
                ]
            )

            wtvl = await mapping_one(
                db,
                """
                SELECT
                    id,
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
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "transfer_line_id":
                        int(
                            transfer_line[
                                "id"
                            ]
                        ),
                },
            )

            source_ice = await mapping_one(
                db,
                """
                SELECT
                    id,
                    valuation_method::text
                        AS valuation_method,
                    quantity,
                    unit_cost,
                    valuation_amount
                FROM inventory_cost_entries
                WHERE company_id = :company_id
                  AND document_id = :document_id
                  AND document_line_id = :document_line_id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "document_id":
                        source_issue_document_id,
                    "document_line_id":
                        source_issue_line_id,
                },
            )

            source_ice_id = int(
                source_ice[
                    "id"
                ]
            )

            assert (
                source_ice[
                    "valuation_method"
                ]
                == "weighted_average_moving"
            )

            assert (
                Decimal(
                    source_ice[
                        "quantity"
                    ]
                )
                == TRANSFER_QTY
            )

            assert q8(
                source_ice[
                    "valuation_amount"
                ]
            ) == Decimal(
                "50.00000000"
            )

            assert (
                int(
                    wtvl[
                        "source_inventory_cost_entry_id"
                    ]
                )
                == source_ice_id
            )

            assert (
                wtvl[
                    "source_stock_lot_consumption_id"
                ]
                is None
            )

            assert q8(
                wtvl[
                    "valuation_amount"
                ]
            ) == Decimal(
                "50.00000000"
            )

            destination_receipt_line_id = int(
                wtvl[
                    "destination_receipt_document_line_id"
                ]
            )

            assert (
                int(
                    wtvl[
                        "destination_receipt_document_id"
                    ]
                )
                == destination_receipt_document_id
            )

            destination_ice = await mapping_one(
                db,
                """
                SELECT
                    id,
                    valuation_method::text
                        AS valuation_method,
                    quantity,
                    unit_cost,
                    valuation_amount
                FROM inventory_cost_entries
                WHERE company_id = :company_id
                  AND document_id = :document_id
                  AND document_line_id = :document_line_id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "document_id":
                        destination_receipt_document_id,
                    "document_line_id":
                        destination_receipt_line_id,
                },
            )

            assert (
                destination_ice[
                    "valuation_method"
                ]
                == "weighted_average_moving"
            )

            assert (
                Decimal(
                    destination_ice[
                        "quantity"
                    ]
                )
                == TRANSFER_QTY
            )

            assert q8(
                destination_ice[
                    "valuation_amount"
                ]
            ) == Decimal(
                "50.00000000"
            )

            source_after_transfer = (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        source_warehouse_id
                    ),
                )
            )

            destination_after_transfer = (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        destination_warehouse_id
                    ),
                )
            )

            assert (
                source_after_transfer[
                    "quantity"
                ]
                == EXPECTED_SOURCE_QTY
            )

            assert q8(
                source_after_transfer[
                    "inventory_value"
                ]
            ) == EXPECTED_SOURCE_ORIGINAL_VALUE

            assert (
                destination_after_transfer[
                    "quantity"
                ]
                == EXPECTED_DESTINATION_QTY
            )

            assert q8(
                destination_after_transfer[
                    "inventory_value"
                ]
            ) == EXPECTED_DESTINATION_ORIGINAL_VALUE

            transfer_journal_count = int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM journal_entries
                    WHERE company_id = :company_id
                      AND document_id = ANY(:document_ids)
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "document_ids": [
                            source_issue_document_id,
                            destination_receipt_document_id,
                        ],
                    },
                )
            )

            assert transfer_journal_count == 0

            print(
                "D2 REAL MA TRANSFER 50 = PASS"
            )

            print(
                "SOURCE ICE -> WTVL -> "
                "DESTINATION ICE = PASS"
            )

            print(
                "TRANSFER JOURNAL = ZERO PASS"
            )

            # ==================================================
            # D. SNAPSHOT IMMUTABLE TRANSFER PROVENANCE
            # ==================================================

            source_issue_movement = await mapping_one(
                db,
                """
                SELECT
                    id,
                    quantity_delta,
                    value_delta,
                    unit_cost,
                    balance_quantity_after,
                    balance_value_after,
                    average_unit_cost_after
                FROM moving_average_movements
                WHERE company_id = :company_id
                  AND document_id = :document_id
                  AND document_line_id = :document_line_id
                  AND movement_type::text = 'issue'
                  AND reversal_of_id IS NULL
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "document_id":
                        source_issue_document_id,
                    "document_line_id":
                        source_issue_line_id,
                },
            )

            destination_receipt_movement = await mapping_one(
                db,
                """
                SELECT
                    id,
                    quantity_delta,
                    value_delta,
                    unit_cost,
                    balance_quantity_after,
                    balance_value_after,
                    average_unit_cost_after
                FROM moving_average_movements
                WHERE company_id = :company_id
                  AND document_id = :document_id
                  AND document_line_id = :document_line_id
                  AND movement_type::text = 'receipt'
                  AND reversal_of_id IS NULL
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "document_id":
                        destination_receipt_document_id,
                    "document_line_id":
                        destination_receipt_line_id,
                },
            )

            source_issue_snapshot = dict(
                source_issue_movement
            )

            destination_receipt_snapshot = dict(
                destination_receipt_movement
            )

            source_ice_snapshot = dict(
                source_ice
            )

            destination_ice_snapshot = dict(
                destination_ice
            )

            wtvl_snapshot = dict(
                wtvl
            )

            # ==================================================
            # E. D3 REAL PVC 120 -> 108
            # ==================================================

            correction = (
                await base.insert_value_correction(
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
                    product_id=product_id,
                    correction_date=d3,
                    original_gross_amount=(
                        "120.00"
                    ),
                    corrected_gross_amount=(
                        "108.00"
                    ),
                    reason_code=(
                        "postgres_ma_transfer_on_hand"
                    ),
                )
            )

            allocation_result = (
                await ma.reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction.id
                    ),
                    adjustment_date=d3,
                    created_by=USER_ID,
                )
            )

            assert (
                allocation_result.source_is_active
                is True
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

            allocation_event_id = int(
                pvc_allocation.id
            )

            assert q8(
                Decimal(
                    pvc_allocation
                    .corrected_allocated_base_amount
                )
                - Decimal(
                    pvc_allocation
                    .original_allocated_base_amount
                )
            ) == PVC_DELTA

            print(
                "D3 REAL PVCA 120 -> 108 = PASS"
            )

            # ==================================================
            # F. PRODUCTION MA PEER RECONCILIATION
            # ==================================================

            reconciliation = (
                await ma.reconcile_purchase_value_correction_moving_average_peers(
                    db,
                    company_id=COMPANY_ID,
                    anchor_allocation_event_id=(
                        allocation_event_id
                    ),
                    adjustment_date=d3,
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert (
                reconciliation
                .anchor_allocation_event_id
                == allocation_event_id
            )

            active = await active_replay_events(
                db,
                allocation_event_id=(
                    allocation_event_id
                ),
            )

            assert len(active) == 2

            assert {
                row[
                    "effect_kind"
                ]
                for row in active
            } == {
                "on_hand"
            }

            by_warehouse = {
                int(
                    row[
                        "warehouse_id"
                    ]
                ): row
                for row in active
            }

            assert set(
                by_warehouse
            ) == {
                source_warehouse_id,
                destination_warehouse_id,
            }

            source_effect = (
                by_warehouse[
                    source_warehouse_id
                ]
            )

            destination_effect = (
                by_warehouse[
                    destination_warehouse_id
                ]
            )

            assert (
                Decimal(
                    source_effect[
                        "quantity"
                    ]
                )
                == EXPECTED_SOURCE_QTY
            )

            assert q8(
                source_effect[
                    "original_valuation_amount"
                ]
            ) == EXPECTED_SOURCE_ORIGINAL_VALUE

            assert q8(
                source_effect[
                    "corrected_valuation_amount"
                ]
            ) == EXPECTED_SOURCE_CORRECTED_VALUE

            assert (
                source_effect[
                    "source_moving_average_movement_id"
                ]
                is None
            )

            assert (
                source_effect[
                    "source_inventory_cost_entry_id"
                ]
                is None
            )

            assert (
                Decimal(
                    destination_effect[
                        "quantity"
                    ]
                )
                == EXPECTED_DESTINATION_QTY
            )

            assert q8(
                destination_effect[
                    "original_valuation_amount"
                ]
            ) == EXPECTED_DESTINATION_ORIGINAL_VALUE

            assert q8(
                destination_effect[
                    "corrected_valuation_amount"
                ]
            ) == EXPECTED_DESTINATION_CORRECTED_VALUE

            assert (
                destination_effect[
                    "source_moving_average_movement_id"
                ]
                is None
            )

            assert (
                destination_effect[
                    "source_inventory_cost_entry_id"
                ]
                is None
            )

            total_delta = sum(
                (
                    q8(
                        row[
                            "corrected_valuation_amount"
                        ]
                    )
                    - q8(
                        row[
                            "original_valuation_amount"
                        ]
                    )
                    for row in active
                ),
                Decimal(
                    "0.00000000"
                ),
            )

            assert q8(
                total_delta
            ) == PVC_DELTA

            assert q8(
                source_effect[
                    "corrected_valuation_amount"
                ]
            ) + q8(
                destination_effect[
                    "corrected_valuation_amount"
                ]
            ) == CORRECTED_VALUE

            assert (
                Decimal(
                    source_effect[
                        "quantity"
                    ]
                )
                + Decimal(
                    destination_effect[
                        "quantity"
                    ]
                )
                == PURCHASE_QTY
            )

            print(
                "PVC SOURCE ON_HAND "
                "70 -> 63 = PASS"
            )

            print(
                "PVC DESTINATION ON_HAND "
                "50 -> 45 = PASS"
            )

            print(
                "PVC TOTAL VALUE "
                "120 -> 108 = PASS"
            )

            # ==================================================
            # G. SOURCE TRANSFER ISSUE MUST NOT BE FINAL TARGET
            # ==================================================

            source_transfer_final_count = int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM purchase_value_correction_ma_replay_events e
                    WHERE e.company_id = :company_id
                      AND e.purchase_value_correction_allocation_event_id
                          = :allocation_event_id
                      AND e.reversal_of_id IS NULL
                      AND e.source_inventory_cost_entry_id
                          = :source_ice_id
                      AND NOT EXISTS (
                          SELECT 1
                          FROM purchase_value_correction_ma_replay_events r
                          WHERE r.company_id = e.company_id
                            AND r.reversal_of_id = e.id
                      )
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "allocation_event_id":
                            allocation_event_id,
                        "source_ice_id":
                            source_ice_id,
                    },
                )
            )

            assert (
                source_transfer_final_count
                == 0
            )

            print(
                "SOURCE TRANSFER ISSUE "
                "FINAL PVC TARGET = ZERO PASS"
            )

            # ==================================================
            # H. IMMUTABLE TRANSFER PROVENANCE
            # ==================================================

            source_issue_after = dict(
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        quantity_delta,
                        value_delta,
                        unit_cost,
                        balance_quantity_after,
                        balance_value_after,
                        average_unit_cost_after
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "id":
                            int(
                                source_issue_snapshot[
                                    "id"
                                ]
                            ),
                    },
                )
            )

            destination_receipt_after = dict(
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        quantity_delta,
                        value_delta,
                        unit_cost,
                        balance_quantity_after,
                        balance_value_after,
                        average_unit_cost_after
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "id":
                            int(
                                destination_receipt_snapshot[
                                    "id"
                                ]
                            ),
                    },
                )
            )

            source_ice_after = dict(
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        valuation_method::text
                            AS valuation_method,
                        quantity,
                        unit_cost,
                        valuation_amount
                    FROM inventory_cost_entries
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "id":
                            source_ice_id,
                    },
                )
            )

            destination_ice_after = dict(
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        valuation_method::text
                            AS valuation_method,
                        quantity,
                        unit_cost,
                        valuation_amount
                    FROM inventory_cost_entries
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "id":
                            int(
                                destination_ice[
                                    "id"
                                ]
                            ),
                    },
                )
            )

            wtvl_after = dict(
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        quantity,
                        unit_cost,
                        valuation_amount,
                        source_inventory_cost_entry_id,
                        source_stock_lot_consumption_id,
                        destination_receipt_document_id,
                        destination_receipt_document_line_id
                    FROM warehouse_transfer_valuation_layers
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "id":
                            int(
                                wtvl[
                                    "id"
                                ]
                            ),
                    },
                )
            )

            assert (
                source_issue_after
                == source_issue_snapshot
            )

            assert (
                destination_receipt_after
                == destination_receipt_snapshot
            )

            assert (
                source_ice_after
                == source_ice_snapshot
            )

            assert (
                destination_ice_after
                == destination_ice_snapshot
            )

            assert (
                wtvl_after
                == wtvl_snapshot
            )

            print(
                "TRANSFER MA / ICE / WTVL "
                "IMMUTABILITY = PASS"
            )

            # ==================================================
            # I. IDEMPOTENCY
            # ==================================================

            active_before_second = tuple(
                (
                    int(row["id"]),
                    int(row["warehouse_id"]),
                    row["effect_kind"],
                    q8(
                        row[
                            "original_valuation_amount"
                        ]
                    ),
                    q8(
                        row[
                            "corrected_valuation_amount"
                        ]
                    ),
                )
                for row in active
            )

            second = (
                await ma.reconcile_purchase_value_correction_moving_average_peers(
                    db,
                    company_id=COMPANY_ID,
                    anchor_allocation_event_id=(
                        allocation_event_id
                    ),
                    adjustment_date=d3,
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert (
                second.created_events
                == ()
            )

            active_after_second = (
                await active_replay_events(
                    db,
                    allocation_event_id=(
                        allocation_event_id
                    ),
                )
            )

            assert tuple(
                (
                    int(row["id"]),
                    int(row["warehouse_id"]),
                    row["effect_kind"],
                    q8(
                        row[
                            "original_valuation_amount"
                        ]
                    ),
                    q8(
                        row[
                            "corrected_valuation_amount"
                        ]
                    ),
                )
                for row in active_after_second
            ) == active_before_second

            print(
                "SECOND IDENTICAL MA RECONCILIATION "
                "= FULL NOOP PASS"
            )

            print()
            print(
                "REAL POSTGRESQL MA TRANSFER ↔ PVC "
                "ON_HAND = PASS"
            )

        except BaseException as exc:
            scenario_error = exc
            import traceback

            scenario_traceback = (
                traceback.format_exc()
            )

        finally:
            await db.close()

            if transaction.is_active:
                await transaction.rollback()

    # ======================================================
    # J. OUTER TRANSACTION ROLLBACK
    # ======================================================

    after = await complete_baseline()

    assert after == baseline, (
        "\nMA Transfer PVC PostgreSQL ON_HAND rollback "
        "did not restore exact baseline.\n"
        f"before={baseline}\n"
        f"after={after}"
    )

    print(
        "REAL POSTGRESQL MA TRANSFER PVC "
        "OUTER ROLLBACK = PASS"
    )

    if scenario_error is not None:
        pytest.fail(
            "\nREAL POSTGRESQL MA TRANSFER PVC "
            "ON_HAND FAILED\n"
            + (
                scenario_traceback
                or repr(
                    scenario_error
                )
            )
        )
