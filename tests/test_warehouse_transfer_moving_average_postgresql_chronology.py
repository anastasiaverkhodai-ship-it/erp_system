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
from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.document_line import DocumentLine
from app.services.warehouse_transfer_history_service import (
    WarehouseTransferLineTarget,
    normalize_transfer_target,
)
from app.services.warehouse_transfer_posting_service import (
    post_warehouse_transfer_document,
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

RECEIPT_1_QTY = Decimal("1.0000")
RECEIPT_1_PRICE = Decimal("1.0000")

RECEIPT_2_QTY = Decimal("2.0000")
RECEIPT_2_PRICE = Decimal("2.0000")

TRANSFER_QTY = Decimal("2.0000")

EXPECTED_SOURCE_VALUE = Decimal("5.00000000")
EXPECTED_SOURCE_AVERAGE = Decimal("1.66666667")
EXPECTED_TRANSFER_VALUE = Decimal("3.33333334")
EXPECTED_SOURCE_REMAINING_VALUE = Decimal("1.66666666")


pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run real warehouse-transfer moving-average "
        "PostgreSQL chronology"
    ),
)


def _load_ma_harness():
    path = Path(__file__).with_name(
        "test_purchase_value_correction_moving_average_gl_"
        "postgresql_chronology.py"
    )

    name = (
        "_warehouse_transfer_ma_postgresql_base"
    )

    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "Could not load proven moving-average "
            "PostgreSQL harness"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    sys.modules[name] = module

    spec.loader.exec_module(
        module
    )

    return module


ma = _load_ma_harness()
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


def q8(
    value,
):
    return Decimal(
        value
    ).quantize(
        Q8
    )


async def complete_baseline():
    existing = (
        await base.complete_table_counts()
    )

    extra_tables = (
        "moving_average_balances",
        "moving_average_movements",
        "inventory_cost_entries",
        "warehouse_transfer_events",
        "warehouse_transfer_lines",
        "warehouse_transfer_valuation_layers",
    )

    extra = {}

    async with engine.connect() as connection:
        for table_name in extra_tables:
            extra[
                table_name
            ] = (
                await connection.execute(
                    text(
                        f"""
                        SELECT COUNT(*)
                        FROM {table_name}
                        """
                    )
                )
            ).scalar_one()

    return {
        "base": existing,
        "warehouse_ma": extra,
    }


async def ma_balance(
    db,
    *,
    product_id,
    warehouse_id,
):
    row = (
        await db.execute(
            text(
                """
                SELECT
                    quantity,
                    inventory_value,
                    average_unit_cost
                FROM moving_average_balances
                WHERE company_id = :company_id
                  AND product_id = :product_id
                  AND warehouse_id = :warehouse_id
                """
            ),
            {
                "company_id": COMPANY_ID,
                "product_id": product_id,
                "warehouse_id": warehouse_id,
            },
        )
    ).mappings().one_or_none()

    if row is None:
        return None

    return {
        "quantity":
            Decimal(row["quantity"]),
        "inventory_value":
            q8(row["inventory_value"]),
        "average_unit_cost":
            q8(row["average_unit_cost"]),
    }


async def active_ma_movements(
    db,
    *,
    product_id,
    warehouse_id,
):
    return await rows(
        db,
        """
        SELECT
            m.id,
            m.document_id,
            m.document_line_id,
            m.movement_type::text AS movement_type,
            m.movement_date,
            m.quantity_delta,
            m.value_delta,
            m.unit_cost,
            m.balance_quantity_after,
            m.balance_value_after,
            m.average_unit_cost_after,
            m.reversal_of_id
        FROM moving_average_movements m
        WHERE m.company_id = :company_id
          AND m.product_id = :product_id
          AND m.warehouse_id = :warehouse_id
          AND NOT EXISTS (
              SELECT 1
              FROM moving_average_movements r
              WHERE r.company_id = m.company_id
                AND r.reversal_of_id = m.id
          )
        ORDER BY m.id
        """,
        {
            "company_id": COMPANY_ID,
            "product_id": product_id,
            "warehouse_id": warehouse_id,
        },
    )


async def document_status(
    db,
    document_id,
):
    return str(
        await scalar(
            db,
            """
            SELECT status::text
            FROM documents
            WHERE company_id = :company_id
              AND id = :document_id
            """,
            {
                "company_id": COMPANY_ID,
                "document_id": document_id,
            },
        )
    ).lower()


async def create_source_receipt(
    db,
    *,
    number,
    document_date,
    product_id,
    warehouse_id,
    quantity,
    price,
):
    document = Document(
        company_id=COMPANY_ID,
        accounting_rule_id=None,
        number=number,
        document_type=(
            DocumentType.RECEIPT
        ),
        document_date=document_date,
        status=DocumentStatus.DRAFT,
        created_by=USER_ID,
    )

    db.add(document)

    await db.flush()

    assert document.id is not None

    line = DocumentLine(
        document_id=document.id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity=quantity,
        price=price,
    )

    db.add(line)

    await db.flush()

    assert line.id is not None

    await post_warehouse_transfer_document(
        db,
        document=document,
        created_by=USER_ID,
    )

    await db.flush()

    assert (
        await document_status(
            db,
            int(document.id),
        )
        == "posted"
    )

    return (
        int(document.id),
        int(line.id),
    )


@pytest.mark.asyncio
async def test_warehouse_transfer_moving_average_postgresql_chronology():
    """
    REAL PostgreSQL moving-average warehouse transfer.

    Source setup:
        R1: 1 @ 1.0000
        R2: 2 @ 2.0000

        source quantity = 3
        source value    = 5.00000000
        MA unit cost    = 1.66666667

    Transfer:
        2 units

        exact MA ISSUE valuation:
            2 * 1.66666667
            = 3.33333334

    Critical precision invariant:

        transfer destination DocumentLine.price is Q4:
            1.6667

        quantity * persisted Q4 price:
            3.33340000

        THIS IS NOT:
            3.33333334

        Destination receipt valuation MUST therefore be driven by
        exact_receipt_valuation_amount / WTVL Q8 provenance, not
        quantity * DocumentLine.price.

    Provenance invariant:

        source ISSUE InventoryCostEntry.valuation_amount
        ==
        WarehouseTransferValuationLayer.valuation_amount
        ==
        destination MA RECEIPT movement.value_delta
        ==
        destination MA balance.inventory_value

    Reversal:
        destination receipt is latest destination MA movement;
        source issue is latest source MA movement.

        production reversal therefore succeeds destination first,
        source second, restoring:
            source quantity/value to pre-transfer state
            destination quantity/value to zero

        immutable original MA movements and WTVL remain present,
        with reversal MA movements linked through reversal_of_id.

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
            # A. REAL BUSINESS FIXTURE + TRANSACTIONAL MA SWITCH
            # ==================================================

            fixture = (
                await base.base.create_business_fixture(
                    db
                )
            )

            await ma.set_company_to_moving_average(
                db
            )

            method = await scalar(
                db,
                """
                SELECT inventory_valuation_method
                FROM companies
                WHERE id = :company_id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                },
            )

            assert (
                str(method)
                == "weighted_average_moving"
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

            d4 = (
                d1
                + timedelta(days=3)
            )

            period_end = (
                await base.base.open_period_end(
                    db,
                    business_date=d1,
                )
            )

            assert period_end is not None

            if d4 > period_end:
                pytest.skip(
                    "Real MA warehouse-transfer E2E "
                    "requires four usable business dates "
                    "inside the open accounting period"
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

            # ----------------------------------------------
            # Transaction-local clean product.
            #
            # Direct SQL here creates MASTER FIXTURE DATA only.
            # It does not create or mutate stock, costing,
            # transfer history, or MA history.
            # ----------------------------------------------

            token = uuid4().hex[:12]

            product_id = int(
                await scalar(
                    db,
                    """
                    INSERT INTO products (
                        company_id,
                        name,
                        sku,
                        is_active
                    )
                    VALUES (
                        :company_id,
                        :name,
                        :sku,
                        TRUE
                    )
                    RETURNING id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "name":
                            (
                                "MA Transfer E2E "
                                + token
                            ),
                        "sku":
                            (
                                "MA-WT-"
                                + token
                            ),
                    },
                )
            )

            assert (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=source_warehouse_id,
                )
                is None
            )

            assert (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=destination_warehouse_id,
                )
                is None
            )

            print(
                "MA E2E CLEAN PRODUCT "
                f"product={product_id} "
                f"source={source_warehouse_id} "
                f"destination={destination_warehouse_id} "
                "= PASS"
            )

            # ==================================================
            # B. R1 REAL SOURCE MA RECEIPT 1 @ 1
            # ==================================================

            (
                receipt_1_document_id,
                receipt_1_line_id,
            ) = await create_source_receipt(
                db,
                number=(
                    "WT-MA-PG-R1-"
                    + token
                ),
                document_date=d1,
                product_id=product_id,
                warehouse_id=(
                    source_warehouse_id
                ),
                quantity=RECEIPT_1_QTY,
                price=RECEIPT_1_PRICE,
            )

            balance_r1 = await ma_balance(
                db,
                product_id=product_id,
                warehouse_id=(
                    source_warehouse_id
                ),
            )

            assert balance_r1 == {
                "quantity":
                    Decimal("1.0000"),
                "inventory_value":
                    Decimal("1.00000000"),
                "average_unit_cost":
                    Decimal("1.00000000"),
            }

            print(
                "R1 REAL MA RECEIPT "
                "1 @ 1.0000 = PASS"
            )

            # ==================================================
            # C. R2 REAL SOURCE MA RECEIPT 2 @ 2
            # ==================================================

            (
                receipt_2_document_id,
                receipt_2_line_id,
            ) = await create_source_receipt(
                db,
                number=(
                    "WT-MA-PG-R2-"
                    + token
                ),
                document_date=d2,
                product_id=product_id,
                warehouse_id=(
                    source_warehouse_id
                ),
                quantity=RECEIPT_2_QTY,
                price=RECEIPT_2_PRICE,
            )

            balance_before_transfer = (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        source_warehouse_id
                    ),
                )
            )

            assert balance_before_transfer[
                "quantity"
            ] == Decimal(
                "3.0000"
            )

            assert balance_before_transfer[
                "inventory_value"
            ] == EXPECTED_SOURCE_VALUE

            assert balance_before_transfer[
                "average_unit_cost"
            ] == EXPECTED_SOURCE_AVERAGE

            print(
                "SOURCE MA BEFORE TRANSFER "
                "quantity=3.0000 "
                "value=5.00000000 "
                "average=1.66666667 "
                "= EXACT PASS"
            )

            source_movements_before = (
                await active_ma_movements(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        source_warehouse_id
                    ),
                )
            )

            assert len(
                source_movements_before
            ) == 2

            # ==================================================
            # D. D3 REAL TRANSFER
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
                transfer_date=d3,
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
                "D3 REAL MA TRANSFER CREATE = PASS"
            )

            # ==================================================
            # E. LOAD IMMUTABLE TRANSFER HISTORY
            # ==================================================

            event = await mapping_one(
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
                event["reversal_of_id"]
                is None
            )

            original_event_id = int(
                event["id"]
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
                        original_event_id,
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

            # ==================================================
            # F. SOURCE ISSUE ICE = EXACT Q8 SOURCE TRUTH
            # ==================================================

            source_ice = await mapping_one(
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

            assert (
                str(
                    source_ice[
                        "valuation_method"
                    ]
                )
                == "weighted_average_moving"
            )

            assert (
                Decimal(
                    source_ice["quantity"]
                )
                == TRANSFER_QTY
            )

            source_ice_unit_cost = q8(
                source_ice[
                    "unit_cost"
                ]
            )

            source_ice_value = q8(
                source_ice[
                    "valuation_amount"
                ]
            )

            assert (
                source_ice_unit_cost
                == EXPECTED_SOURCE_AVERAGE
            )

            assert (
                source_ice_value
                == EXPECTED_TRANSFER_VALUE
            )

            source_ice_id = int(
                source_ice["id"]
            )

            print(
                "SOURCE ISSUE ICE "
                f"id={source_ice_id} "
                f"unit_cost={source_ice_unit_cost} "
                f"value={source_ice_value} "
                "= EXACT Q8 PASS"
            )

            # ==================================================
            # G. SOURCE MA MOVEMENT/BALANCE AFTER ISSUE
            # ==================================================

            source_after = await ma_balance(
                db,
                product_id=product_id,
                warehouse_id=(
                    source_warehouse_id
                ),
            )

            assert source_after[
                "quantity"
            ] == Decimal(
                "1.0000"
            )

            assert source_after[
                "inventory_value"
            ] == EXPECTED_SOURCE_REMAINING_VALUE

            assert source_after[
                "average_unit_cost"
            ] == EXPECTED_SOURCE_AVERAGE

            source_issue_movement = (
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
                      AND document_id = :document_id
                      AND document_line_id = :document_line_id
                      AND movement_type::text = 'issue'
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
            )

            assert q8(
                -Decimal(
                    source_issue_movement[
                        "value_delta"
                    ]
                )
            ) == EXPECTED_TRANSFER_VALUE

            assert q8(
                source_issue_movement[
                    "unit_cost"
                ]
            ) == EXPECTED_SOURCE_AVERAGE

            print(
                "SOURCE MA ISSUE MOVEMENT "
                "= EXACT PASS"
            )

            # ==================================================
            # H. WTVL MUST COPY SOURCE ICE EXACTLY
            # ==================================================

            wtvl = await mapping_one(
                db,
                """
                SELECT
                    id,
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
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "transfer_line_id":
                        int(
                            transfer_line["id"]
                        ),
                },
            )

            assert (
                str(
                    wtvl[
                        "valuation_method"
                    ]
                )
                == "weighted_average_moving"
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

            assert (
                Decimal(
                    wtvl["quantity"]
                )
                == TRANSFER_QTY
            )

            assert q8(
                wtvl[
                    "unit_cost"
                ]
            ) == source_ice_unit_cost

            wtvl_value = q8(
                wtvl[
                    "valuation_amount"
                ]
            )

            assert (
                wtvl_value
                == source_ice_value
                == EXPECTED_TRANSFER_VALUE
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

            print(
                "WTVL SOURCE ICE PROVENANCE "
                f"value={wtvl_value} "
                "= EXACT PASS"
            )

            # ==================================================
            # I. PROVE Q4 DOCUMENT LINE WOULD DRIFT
            # ==================================================

            destination_line = await mapping_one(
                db,
                """
                SELECT
                    quantity,
                    price
                FROM document_lines
                WHERE document_id = :document_id
                  AND id = :line_id
                """,
                {
                    "document_id":
                        destination_receipt_document_id,
                    "line_id":
                        destination_receipt_line_id,
                },
            )

            persisted_quantity = Decimal(
                destination_line[
                    "quantity"
                ]
            )

            persisted_q4_price = Decimal(
                destination_line[
                    "price"
                ]
            )

            reconstructed_q4_value = q8(
                persisted_quantity
                * persisted_q4_price
            )

            assert (
                persisted_quantity
                == TRANSFER_QTY
            )

            assert (
                reconstructed_q4_value
                != source_ice_value
            ), (
                "Test does not exercise Q4 rounding drift; "
                f"Q4 reconstruction unexpectedly equals exact Q8: "
                f"{reconstructed_q4_value}"
            )

            print(
                "DESTINATION DOCUMENTLINE "
                f"price(Q4)={persisted_q4_price} "
                f"quantity={persisted_quantity}"
            )

            print(
                "Q4 RECONSTRUCTED VALUE "
                f"= {reconstructed_q4_value}"
            )

            print(
                "EXACT TRANSFER VALUE "
                f"= {source_ice_value}"
            )

            print(
                "DOCUMENTLINE Q4 ROUNDING DRIFT "
                f"= {q8(reconstructed_q4_value - source_ice_value)}"
            )

            print(
                "Q4 VALUE != EXACT Q8 VALUE "
                "= PROVEN"
            )

            # ==================================================
            # J. DESTINATION MA RECEIPT MUST USE EXACT Q8 VALUE
            # ==================================================

            destination_movement = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        document_id,
                        document_line_id,
                        movement_type::text
                            AS movement_type,
                        quantity_delta,
                        value_delta,
                        unit_cost,
                        balance_quantity_after,
                        balance_value_after,
                        average_unit_cost_after
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND document_id = :document_id
                      AND document_line_id = :line_id
                      AND movement_type::text = 'receipt'
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "document_id":
                            destination_receipt_document_id,
                        "line_id":
                            destination_receipt_line_id,
                    },
                )
            )

            destination_movement_value = q8(
                destination_movement[
                    "value_delta"
                ]
            )

            destination_movement_unit_cost = q8(
                destination_movement[
                    "unit_cost"
                ]
            )

            destination_movement_average_after = q8(
                destination_movement[
                    "average_unit_cost_after"
                ]
            )

            assert (
                destination_movement_value
                == source_ice_value
                == wtvl_value
                == EXPECTED_TRANSFER_VALUE
            )

            assert (
                destination_movement_unit_cost
                == q8(
                    persisted_q4_price
                )
            )

            assert (
                destination_movement_average_after
                == EXPECTED_SOURCE_AVERAGE
            )

            assert (
                destination_movement_unit_cost
                != destination_movement_average_after
            )

            destination_balance = (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        destination_warehouse_id
                    ),
                )
            )

            assert destination_balance[
                "quantity"
            ] == TRANSFER_QTY

            assert destination_balance[
                "inventory_value"
            ] == EXPECTED_TRANSFER_VALUE

            assert destination_balance[
                "average_unit_cost"
            ] == EXPECTED_SOURCE_AVERAGE

            assert (
                destination_balance[
                    "inventory_value"
                ]
                != reconstructed_q4_value
            )

            print(
                "DESTINATION MA MOVEMENT VALUE "
                f"= {destination_movement_value}"
            )

            print(
                "DESTINATION MA BALANCE VALUE "
                f"= {destination_balance['inventory_value']}"
            )

            print(
                "SOURCE ICE == WTVL == "
                "DESTINATION MA MOVEMENT == "
                "DESTINATION MA BALANCE "
                "= EXACT Q8 PASS"
            )

            print(
                "DOCUMENTLINE PRICE Q4 "
                "DID NOT DRIVE MA VALUATION "
                "= PASS"
            )

            # ==================================================
            # K. TRANSFER HAS NO NORMAL ACCOUNTING
            # ==================================================

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

            assert (
                transfer_journal_count
                == 0
            )

            print(
                "TRANSFER JOURNAL ENTRIES = ZERO PASS"
            )

            # ==================================================
            # L. SNAPSHOT IMMUTABLE PRE-REVERSAL PROVENANCE
            # ==================================================

            source_issue_movement_id = int(
                source_issue_movement[
                    "id"
                ]
            )

            destination_movement_id = int(
                destination_movement[
                    "id"
                ]
            )

            original_wtvl_id = int(
                wtvl["id"]
            )

            source_pre_reversal = dict(
                source_after
            )

            destination_pre_reversal = dict(
                destination_balance
            )

            # ==================================================
            # M. D4 CLEAN PRODUCTION TRANSFER REVERSAL
            # ==================================================

            reverse_plan = (
                await execute_warehouse_transfer_reconciliation(
                    db,
                    company_id=COMPANY_ID,
                    history_key=history_key,
                    target=None,
                    adjustment_date=d4,
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert (
                reverse_plan.action.value
                == "reverse"
            )

            print(
                "D4 CLEAN MA TRANSFER REVERSAL = PASS"
            )

            # ==================================================
            # N. DOCUMENT STATUS
            # ==================================================

            assert (
                await document_status(
                    db,
                    source_issue_document_id,
                )
                == "reversed"
            )

            assert (
                await document_status(
                    db,
                    destination_receipt_document_id,
                )
                == "reversed"
            )

            print(
                "TRANSFER DOCUMENTS REVERSED = PASS"
            )

            # ==================================================
            # O. MA BALANCES RESTORED EXACTLY
            # ==================================================

            source_after_reversal = (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        source_warehouse_id
                    ),
                )
            )

            destination_after_reversal = (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        destination_warehouse_id
                    ),
                )
            )

            assert source_after_reversal[
                "quantity"
            ] == Decimal(
                "3.0000"
            )

            assert source_after_reversal[
                "inventory_value"
            ] == EXPECTED_SOURCE_VALUE

            assert source_after_reversal[
                "average_unit_cost"
            ] == EXPECTED_SOURCE_AVERAGE

            assert destination_after_reversal[
                "quantity"
            ] == Decimal(
                "0.0000"
            )

            assert destination_after_reversal[
                "inventory_value"
            ] == Decimal(
                "0.00000000"
            )

            assert destination_after_reversal[
                "average_unit_cost"
            ] == Decimal(
                "0.00000000"
            )

            print(
                "SOURCE MA BALANCE RESTORED "
                "quantity=3 value=5 average=1.66666667 "
                "= EXACT PASS"
            )

            print(
                "DESTINATION MA BALANCE RESTORED TO ZERO "
                "= EXACT PASS"
            )

            # ==================================================
            # P. IMMUTABLE MA REVERSAL PROVENANCE
            # ==================================================

            source_reversal = await mapping_one(
                db,
                """
                SELECT
                    id,
                    reversal_of_id,
                    quantity_delta,
                    value_delta
                FROM moving_average_movements
                WHERE company_id = :company_id
                  AND reversal_of_id = :original_id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "original_id":
                        source_issue_movement_id,
                },
            )

            destination_reversal = await mapping_one(
                db,
                """
                SELECT
                    id,
                    reversal_of_id,
                    quantity_delta,
                    value_delta
                FROM moving_average_movements
                WHERE company_id = :company_id
                  AND reversal_of_id = :original_id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "original_id":
                        destination_movement_id,
                },
            )

            assert (
                int(
                    source_reversal[
                        "reversal_of_id"
                    ]
                )
                == source_issue_movement_id
            )

            assert (
                int(
                    destination_reversal[
                        "reversal_of_id"
                    ]
                )
                == destination_movement_id
            )

            assert q8(
                source_reversal[
                    "value_delta"
                ]
            ) == EXPECTED_TRANSFER_VALUE

            assert q8(
                destination_reversal[
                    "value_delta"
                ]
            ) == -EXPECTED_TRANSFER_VALUE

            print(
                "MA REVERSAL MOVEMENTS "
                "LINK TO ORIGINALS = PASS"
            )

            # Original rows must still exist unchanged.
            original_source_after = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        quantity_delta,
                        value_delta,
                        unit_cost
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "id":
                            source_issue_movement_id,
                    },
                )
            )

            original_destination_after = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        quantity_delta,
                        value_delta,
                        unit_cost
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND id = :id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "id":
                            destination_movement_id,
                    },
                )
            )

            assert q8(
                -Decimal(
                    original_source_after[
                        "value_delta"
                    ]
                )
            ) == EXPECTED_TRANSFER_VALUE

            assert q8(
                original_destination_after[
                    "value_delta"
                ]
            ) == EXPECTED_TRANSFER_VALUE

            assert q8(
                original_source_after[
                    "unit_cost"
                ]
            ) == EXPECTED_SOURCE_AVERAGE

            #
            # Destination receipt movement.unit_cost preserves the
            # posted receipt line's Q4 unit-cost representation.
            #
            # It is NOT the authoritative transfer valuation source.
            # The authoritative economics are:
            #
            #   WTVL valuation_amount
            #   -> exact_receipt_valuation_amount
            #   -> movement.value_delta
            #   -> MA balance inventory_value
            #
            # Therefore:
            #   movement.unit_cost == persisted Q4 DocumentLine.price
            # while:
            #   movement.value_delta remains exact Q8.
            #
            assert q8(
                original_destination_after[
                    "unit_cost"
                ]
            ) == q8(
                persisted_q4_price
            )

            assert q8(
                original_destination_after[
                    "value_delta"
                ]
            ) == EXPECTED_TRANSFER_VALUE

            wtvl_after = await mapping_one(
                db,
                """
                SELECT
                    id,
                    source_inventory_cost_entry_id,
                    valuation_amount
                FROM warehouse_transfer_valuation_layers
                WHERE company_id = :company_id
                  AND id = :id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "id":
                        original_wtvl_id,
                },
            )

            assert (
                int(
                    wtvl_after[
                        "source_inventory_cost_entry_id"
                    ]
                )
                == source_ice_id
            )

            assert q8(
                wtvl_after[
                    "valuation_amount"
                ]
            ) == EXPECTED_TRANSFER_VALUE

            source_ice_after = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
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

            assert q8(
                source_ice_after[
                    "unit_cost"
                ]
            ) == EXPECTED_SOURCE_AVERAGE

            assert q8(
                source_ice_after[
                    "valuation_amount"
                ]
            ) == EXPECTED_TRANSFER_VALUE

            print(
                "ORIGINAL SOURCE ICE = IMMUTABLE"
            )

            print(
                "ORIGINAL WTVL = IMMUTABLE"
            )

            print(
                "ORIGINAL MA MOVEMENTS = IMMUTABLE"
            )

            # ==================================================
            # Q. IMMUTABLE TRANSFER REVERSAL EVENT
            # ==================================================

            history = await rows(
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

            assert len(
                history
            ) == 2

            assert (
                history[0][
                    "reversal_of_id"
                ]
                is None
            )

            assert (
                int(
                    history[1][
                        "reversal_of_id"
                    ]
                )
                == original_event_id
            )

            print(
                "IMMUTABLE TRANSFER REVERSAL EVENT = PASS"
            )

            print()
            print(
                "REAL MOVING AVERAGE POSTGRESQL "
                "TRANSFER CHRONOLOGY = PASS"
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
    # R. EXACT OUTER TRANSACTION ROLLBACK
    # ======================================================

    after = await complete_baseline()

    assert after == baseline, (
        "\nWarehouse Transfer MA PostgreSQL E2E "
        "rollback did not restore exact row-count baseline.\n"
        f"before={baseline}\n"
        f"after={after}"
    )

    print(
        "REAL MA FULL POSTGRESQL "
        "TRANSACTION ROLLBACK = PASS"
    )

    if scenario_error is not None:
        raise scenario_error.with_traceback(
            scenario_traceback
        )
