from __future__ import annotations

import importlib.util
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sys
import traceback
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


COMPANY_ID = 1
USER_ID = 1

INITIAL_QTY_1 = Decimal("1.0000")
INITIAL_PRICE_1 = Decimal("1.0000")

INITIAL_QTY_2 = Decimal("2.0000")
INITIAL_PRICE_2 = Decimal("2.0000")

TRANSFER_QTY = Decimal("2.0000")

LATER_SOURCE_QTY = Decimal("1.0000")
LATER_SOURCE_PRICE = Decimal("4.0000")


def _load_existing_ma_test():
    path = Path(
        "tests/"
        "test_warehouse_transfer_moving_average_"
        "postgresql_chronology.py"
    )

    name = "_warehouse_transfer_ma_pg_for_atomicity"

    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(
        spec
    )

    sys.modules[name] = module

    spec.loader.exec_module(
        module
    )

    return module


wt = _load_existing_ma_test()

ma = wt.ma
base = wt.base


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


async def mapping_all(
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
        ).mappings().all()
    )


async def transfer_table_counts(
    db,
):
    result = {}

    for table in (
        "warehouse_transfer_events",
        "warehouse_transfer_lines",
        "warehouse_transfer_valuation_layers",
    ):
        result[table] = int(
            await scalar(
                db,
                f"SELECT COUNT(*) FROM {table}",
            )
        )

    return result


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


async def ma_balance(
    db,
    *,
    product_id,
    warehouse_id,
):
    result = (
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

    if result is None:
        return None

    return {
        "quantity": Decimal(
            result["quantity"]
        ),
        "inventory_value": Decimal(
            result["inventory_value"]
        ),
        "average_unit_cost": Decimal(
            result["average_unit_cost"]
        ),
    }


async def movement_snapshot(
    db,
    *,
    product_id,
    warehouse_id,
):
    rows = await mapping_all(
        db,
        """
        SELECT
            id,
            document_id,
            document_line_id,
            movement_type::text AS movement_type,
            quantity_delta,
            value_delta,
            unit_cost,
            balance_quantity_after,
            balance_value_after,
            average_unit_cost_after,
            reversal_of_id
        FROM moving_average_movements
        WHERE company_id = :company_id
          AND product_id = :product_id
          AND warehouse_id = :warehouse_id
        ORDER BY id
        """,
        {
            "company_id": COMPANY_ID,
            "product_id": product_id,
            "warehouse_id": warehouse_id,
        },
    )

    return tuple(
        (
            int(row["id"]),
            int(row["document_id"]),
            int(row["document_line_id"]),
            str(row["movement_type"]),
            Decimal(row["quantity_delta"]),
            Decimal(row["value_delta"]),
            Decimal(row["unit_cost"]),
            Decimal(row["balance_quantity_after"]),
            Decimal(row["balance_value_after"]),
            Decimal(row["average_unit_cost_after"]),
            (
                None
                if row["reversal_of_id"] is None
                else int(row["reversal_of_id"])
            ),
        )
        for row in rows
    )


async def stock_balance_quantity(
    db,
    *,
    product_id,
    warehouse_id,
):
    value = await scalar_or_none(
        db,
        """
        SELECT quantity
        FROM stock_balances
        WHERE company_id = :company_id
          AND product_id = :product_id
          AND warehouse_id = :warehouse_id
        """,
        {
            "company_id": COMPANY_ID,
            "product_id": product_id,
            "warehouse_id": warehouse_id,
        },
    )

    if value is None:
        return Decimal("0")

    return Decimal(value)


async def reversal_stock_ledger_count(
    db,
    *,
    document_id,
):
    return int(
        await scalar(
            db,
            """
            SELECT COUNT(*)
            FROM stock_ledger
            WHERE company_id = :company_id
              AND document_id = :document_id
              AND movement_type::text = 'reversal'
            """,
            {
                "company_id": COMPANY_ID,
                "document_id": document_id,
            },
        )
    )


@pytest.mark.asyncio
async def test_warehouse_transfer_atomic_reversal_postgresql():
    """
    Real PostgreSQL atomicity proof.

    Chronology:

        D1 source MA receipt
        D2 source MA receipt
        D3 warehouse transfer source -> destination
        D4 later SOURCE receipt

    D4 makes the transfer source ISSUE no longer the latest
    source MA movement.

    The destination transfer RECEIPT remains the latest movement
    in the destination warehouse.

    Reversal chronology therefore becomes:

        destination RECEIPT reversal -> SUCCESS
        source ISSUE reversal        -> FAIL

    The transfer services deliberately do not own the transaction.

    The caller wraps the failed reversal attempt in a SAVEPOINT.
    Rolling that SAVEPOINT back must remove every effect produced
    by the successful first leg and every temporary effect made
    before the second leg raised.

    No partial reversal may survive.
    """

    if os.getenv(
        "RUN_POSTGRES_E2E"
    ) != "1":
        pytest.skip(
            "Set RUN_POSTGRES_E2E=1 to run real "
            "warehouse-transfer atomic reversal PostgreSQL test"
        )

    # pytest-asyncio may execute each async test on a distinct
    # event loop while app.core.database.engine is module-global.
    #
    # Drop the previous pool before the first DB access in this
    # test so no asyncpg connection created on an earlier test
    # loop can be reused here.
    #
    # This matches the lifecycle isolation already used by the
    # FIFO positive, FIFO negative, and MA PostgreSQL chronology
    # harnesses.
    await engine.dispose(
        close=False
    )

    baseline = await wt.complete_baseline()

    scenario_error = None
    scenario_traceback = None

    async with engine.connect() as connection:
        outer_transaction = await connection.begin()

        db = AsyncSession(
            bind=connection,
            expire_on_commit=False,
        )

        try:
            # ==================================================
            # A. PROVEN BUSINESS FIXTURE + MA MODE
            # ==================================================

            fixture = (
                await base.base.create_business_fixture(
                    db
                )
            )

            await ma.set_company_to_moving_average(
                db
            )

            valuation_method = await scalar(
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

            assert (
                str(valuation_method)
                == "weighted_average_moving"
            )

            d1 = fixture["business_date"]
            d2 = d1 + timedelta(days=1)
            d3 = d1 + timedelta(days=2)
            d4 = d1 + timedelta(days=3)
            d5 = d1 + timedelta(days=4)

            period_end = (
                await base.base.open_period_end(
                    db,
                    business_date=d1,
                )
            )

            assert period_end is not None

            if d5 > period_end:
                pytest.skip(
                    "Atomic reversal PostgreSQL E2E requires "
                    "five usable dates inside open period"
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
                        "company_id": COMPANY_ID,
                        "name":
                            "MA Atomic Transfer " + token,
                        "sku":
                            "MA-WT-ATOMIC-" + token,
                    },
                )
            )

            print(
                "ATOMIC E2E CLEAN PRODUCT "
                f"product={product_id} "
                f"source={source_warehouse_id} "
                f"destination={destination_warehouse_id} "
                "= PASS"
            )

            # ==================================================
            # B. SOURCE MA INVENTORY
            # ==================================================

            await wt.create_source_receipt(
                db,
                number="WT-MA-ATOMIC-R1-" + token,
                document_date=d1,
                product_id=product_id,
                warehouse_id=source_warehouse_id,
                quantity=INITIAL_QTY_1,
                price=INITIAL_PRICE_1,
            )

            await wt.create_source_receipt(
                db,
                number="WT-MA-ATOMIC-R2-" + token,
                document_date=d2,
                product_id=product_id,
                warehouse_id=source_warehouse_id,
                quantity=INITIAL_QTY_2,
                price=INITIAL_PRICE_2,
            )

            await db.flush()

            source_before_transfer = await ma_balance(
                db,
                product_id=product_id,
                warehouse_id=source_warehouse_id,
            )

            assert source_before_transfer is not None
            assert (
                source_before_transfer["quantity"]
                == Decimal("3.0000")
            )
            assert (
                source_before_transfer["inventory_value"]
                == Decimal("5.00000000")
            )
            assert (
                source_before_transfer["average_unit_cost"]
                == Decimal("1.66666667")
            )

            print(
                "SOURCE MA SETUP = PASS"
            )

            # ==================================================
            # C. REAL TRANSFER
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
                    "company_id": COMPANY_ID,
                    "history_key": history_key,
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
                    issue_document_id,
                    issue_document_line_id,
                    receipt_document_id
                FROM warehouse_transfer_lines
                WHERE company_id = :company_id
                  AND transfer_event_id = :event_id
                """,
                {
                    "company_id": COMPANY_ID,
                    "event_id": original_event_id,
                },
            )

            source_issue_document_id = int(
                transfer_line[
                    "issue_document_id"
                ]
            )

            destination_receipt_document_id = int(
                transfer_line[
                    "receipt_document_id"
                ]
            )

            assert (
                await document_status(
                    db,
                    source_issue_document_id,
                )
                == "posted"
            )

            assert (
                await document_status(
                    db,
                    destination_receipt_document_id,
                )
                == "posted"
            )

            print(
                "D3 REAL MA TRANSFER CREATE = PASS"
            )

            # ==================================================
            # D. LATER SOURCE MOVEMENT
            # ==================================================

            later_source_document_id, _ = (
                await wt.create_source_receipt(
                    db,
                    number=(
                        "WT-MA-ATOMIC-LATER-"
                        + token
                    ),
                    document_date=d4,
                    product_id=product_id,
                    warehouse_id=source_warehouse_id,
                    quantity=LATER_SOURCE_QTY,
                    price=LATER_SOURCE_PRICE,
                )
            )

            await db.flush()

            source_issue_movement_id = int(
                await scalar(
                    db,
                    """
                    SELECT id
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND document_id = :document_id
                      AND movement_type::text = 'issue'
                    ORDER BY id
                    LIMIT 1
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "document_id":
                            source_issue_document_id,
                    },
                )
            )

            later_source_movement_id = int(
                await scalar(
                    db,
                    """
                    SELECT id
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND document_id = :document_id
                      AND movement_type::text = 'receipt'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "document_id":
                            later_source_document_id,
                    },
                )
            )

            destination_original_movement_id = int(
                await scalar(
                    db,
                    """
                    SELECT id
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND document_id = :document_id
                      AND movement_type::text = 'receipt'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "document_id":
                            destination_receipt_document_id,
                    },
                )
            )

            assert (
                later_source_movement_id
                > source_issue_movement_id
            )

            destination_latest_id = int(
                await scalar(
                    db,
                    """
                    SELECT id
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND product_id = :product_id
                      AND warehouse_id = :warehouse_id
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "product_id": product_id,
                        "warehouse_id":
                            destination_warehouse_id,
                    },
                )
            )

            assert (
                destination_latest_id
                == destination_original_movement_id
            )

            print(
                "LATER SOURCE MA MOVEMENT = PRESENT"
            )
            print(
                "SOURCE ISSUE IS NOT LATEST = PASS"
            )
            print(
                "DESTINATION RECEIPT REMAINS LATEST = PASS"
            )

            # ==================================================
            # E. SNAPSHOT EXACT PRE-REVERSAL STATE
            # ==================================================

            source_balance_before = await ma_balance(
                db,
                product_id=product_id,
                warehouse_id=source_warehouse_id,
            )

            destination_balance_before = await ma_balance(
                db,
                product_id=product_id,
                warehouse_id=destination_warehouse_id,
            )

            source_stock_before = (
                await stock_balance_quantity(
                    db,
                    product_id=product_id,
                    warehouse_id=source_warehouse_id,
                )
            )

            destination_stock_before = (
                await stock_balance_quantity(
                    db,
                    product_id=product_id,
                    warehouse_id=destination_warehouse_id,
                )
            )

            source_movements_before = await movement_snapshot(
                db,
                product_id=product_id,
                warehouse_id=source_warehouse_id,
            )

            destination_movements_before = (
                await movement_snapshot(
                    db,
                    product_id=product_id,
                    warehouse_id=destination_warehouse_id,
                )
            )

            transfer_event_count_before = int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM warehouse_transfer_events
                    WHERE company_id = :company_id
                      AND history_key = :history_key
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "history_key": history_key,
                    },
                )
            )

            assert (
                transfer_event_count_before
                == 1
            )

            assert (
                await reversal_stock_ledger_count(
                    db,
                    document_id=
                        destination_receipt_document_id,
                )
                == 0
            )

            assert (
                await reversal_stock_ledger_count(
                    db,
                    document_id=
                        source_issue_document_id,
                )
                == 0
            )

            destination_ma_reversal_before = int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND reversal_of_id =
                          :original_movement_id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "original_movement_id":
                            destination_original_movement_id,
                    },
                )
            )

            assert (
                destination_ma_reversal_before
                == 0
            )

            print(
                "PRE-REVERSAL SNAPSHOT = PASS"
            )

            # ==================================================
            # F. CALLER SAVEPOINT
            #
            # Destination leg MUST succeed first.
            # Source leg MUST then fail on MA chronology.
            #
            # No service owns rollback.
            # ==================================================

            savepoint = await db.begin_nested()

            caught = None

            try:
                await execute_warehouse_transfer_reconciliation(
                    db,
                    company_id=COMPANY_ID,
                    history_key=history_key,
                    target=None,
                    adjustment_date=d5,
                    created_by=USER_ID,
                )

            except ValueError as exc:
                caught = exc

                error_text = str(
                    exc
                )

                print(
                    "EXPECTED ATOMIC REVERSAL ERROR = "
                    + error_text
                )

                assert (
                    "source transfer ISSUE reversal failed"
                    in error_text
                )

                assert (
                    "later inventory movements exist"
                    in error_text
                )

            else:
                pytest.fail(
                    "transfer reversal unexpectedly succeeded"
                )

            assert caught is not None

            # --------------------------------------------------
            # IMPORTANT:
            # Before caller rollback, prove first leg actually
            # succeeded and left observable temporary state.
            # --------------------------------------------------

            assert (
                await document_status(
                    db,
                    destination_receipt_document_id,
                )
                == "reversed"
            )

            assert (
                await reversal_stock_ledger_count(
                    db,
                    document_id=
                        destination_receipt_document_id,
                )
                > 0
            )

            temporary_destination_ma_reversal_count = int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND reversal_of_id =
                          :original_movement_id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "original_movement_id":
                            destination_original_movement_id,
                    },
                )
            )

            assert (
                temporary_destination_ma_reversal_count
                == 1
            )

            # Executor appends transfer reversal history only
            # AFTER factory.reverse_transfer() fully succeeds.
            # Source failed, so immutable reversal metadata must
            # not even exist before SAVEPOINT rollback.
            assert int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM warehouse_transfer_events
                    WHERE company_id = :company_id
                      AND history_key = :history_key
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "history_key": history_key,
                    },
                )
            ) == 1

            print(
                "DESTINATION REVERSAL SUCCEEDED "
                "INSIDE SAVEPOINT = PROVEN"
            )

            print(
                "SOURCE REVERSAL FAILED AFTER FIRST LEG = PROVEN"
            )

            print(
                "REVERSAL HISTORY NOT APPENDED = PASS"
            )

            # ==================================================
            # G. CALLER ROLLBACK
            # ==================================================

            await savepoint.rollback()

            # Force all subsequent reads through PostgreSQL.
            db.expire_all()

            print(
                "CALLER SAVEPOINT ROLLBACK EXECUTED"
            )

            # ==================================================
            # H. EXACT STATE RESTORATION
            # ==================================================

            assert (
                await document_status(
                    db,
                    destination_receipt_document_id,
                )
                == "posted"
            )

            assert (
                await document_status(
                    db,
                    source_issue_document_id,
                )
                == "posted"
            )

            assert (
                await reversal_stock_ledger_count(
                    db,
                    document_id=
                        destination_receipt_document_id,
                )
                == 0
            )

            assert (
                await reversal_stock_ledger_count(
                    db,
                    document_id=
                        source_issue_document_id,
                )
                == 0
            )

            assert int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND reversal_of_id =
                          :original_movement_id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "original_movement_id":
                            destination_original_movement_id,
                    },
                )
            ) == 0

            assert (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=source_warehouse_id,
                )
                == source_balance_before
            )

            assert (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=
                        destination_warehouse_id,
                )
                == destination_balance_before
            )

            assert (
                await stock_balance_quantity(
                    db,
                    product_id=product_id,
                    warehouse_id=source_warehouse_id,
                )
                == source_stock_before
            )

            assert (
                await stock_balance_quantity(
                    db,
                    product_id=product_id,
                    warehouse_id=
                        destination_warehouse_id,
                )
                == destination_stock_before
            )

            assert (
                await movement_snapshot(
                    db,
                    product_id=product_id,
                    warehouse_id=source_warehouse_id,
                )
                == source_movements_before
            )

            assert (
                await movement_snapshot(
                    db,
                    product_id=product_id,
                    warehouse_id=
                        destination_warehouse_id,
                )
                == destination_movements_before
            )

            assert int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM warehouse_transfer_events
                    WHERE company_id = :company_id
                      AND history_key = :history_key
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "history_key": history_key,
                    },
                )
            ) == transfer_event_count_before

            # Original transfer-domain provenance remains intact.
            assert int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM warehouse_transfer_lines
                    WHERE company_id = :company_id
                      AND transfer_event_id = :event_id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "event_id": original_event_id,
                    },
                )
            ) == 1

            assert int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM warehouse_transfer_valuation_layers
                    WHERE company_id = :company_id
                      AND transfer_line_id IN (
                          SELECT id
                          FROM warehouse_transfer_lines
                          WHERE company_id = :company_id
                            AND transfer_event_id = :event_id
                      )
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "event_id": original_event_id,
                    },
                )
            ) == 1

            # Later source movement that caused failure survives
            # because it predates the SAVEPOINT.
            assert int(
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND id = :movement_id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "movement_id":
                            later_source_movement_id,
                    },
                )
            ) == 1

            assert (
                await document_status(
                    db,
                    later_source_document_id,
                )
                == "posted"
            )

            print(
                "DESTINATION DOCUMENT RESTORED TO POSTED = PASS"
            )

            print(
                "SOURCE DOCUMENT REMAINS POSTED = PASS"
            )

            print(
                "DESTINATION REVERSAL MOVEMENTS ROLLED BACK = PASS"
            )

            print(
                "SOURCE TEMPORARY REVERSAL CHANGES ROLLED BACK = PASS"
            )

            print(
                "SOURCE MA BALANCE EXACTLY RESTORED = PASS"
            )

            print(
                "DESTINATION MA BALANCE EXACTLY RESTORED = PASS"
            )

            print(
                "SOURCE STOCK BALANCE EXACTLY RESTORED = PASS"
            )

            print(
                "DESTINATION STOCK BALANCE EXACTLY RESTORED = PASS"
            )

            print(
                "SOURCE MA MOVEMENT HISTORY EXACTLY RESTORED = PASS"
            )

            print(
                "DESTINATION MA MOVEMENT HISTORY EXACTLY RESTORED = PASS"
            )

            print(
                "NO TRANSFER REVERSAL HISTORY EVENT = PASS"
            )

            print(
                "ORIGINAL IMMUTABLE TRANSFER PROVENANCE = PRESERVED"
            )

            print(
                "LATER SOURCE MOVEMENT = PRESERVED"
            )

            print(
                "ATOMIC PARTIAL REVERSAL ROLLBACK = PASS"
            )

        except BaseException as exc:
            scenario_error = exc

            scenario_traceback = (
                traceback.format_exc()
            )

        finally:
            await db.close()

            if outer_transaction.is_active:
                await outer_transaction.rollback()

    after = await wt.complete_baseline()

    assert after == baseline

    print(
        "REAL ATOMIC E2E FULL POSTGRESQL "
        "TRANSACTION ROLLBACK = PASS"
    )

    if scenario_error is not None:
        assert scenario_traceback is not None
        print(
            scenario_traceback
        )
        raise scenario_error
