import importlib.util
import os
from pathlib import Path
import sys
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

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
TRANSFER_QTY = Decimal("50.0000")
TRANSFER_LOT_TOUCH_QTY = Decimal("1.0000")
BALANCE_TOP_UP_QTY = Decimal("1.0000")


pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run real warehouse-transfer FIFO "
        "negative PostgreSQL chronology"
    ),
)


def _load_positive_transfer_harness():
    path = Path(__file__).with_name(
        "test_warehouse_transfer_fifo_postgresql_chronology.py"
    )

    module_name = (
        "_warehouse_transfer_fifo_positive_pg_base"
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
            "Could not load positive warehouse-transfer "
            "FIFO PostgreSQL harness"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    sys.modules[module_name] = module

    spec.loader.exec_module(
        module
    )

    return module


positive = _load_positive_transfer_harness()

fifo = positive.fifo
rows = positive.rows
scalar = positive.scalar
complete_baseline = positive.complete_baseline


async def source_fifo_snapshot(
    db,
    *,
    product_id,
    warehouse_id,
):
    result = await rows(
        db,
        """
        SELECT
            id,
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
            "warehouse_id": warehouse_id,
        },
    )

    return tuple(
        (
            int(row["id"]),
            Decimal(
                row["original_quantity"]
            ),
            Decimal(
                row["remaining_quantity"]
            ),
            Decimal(
                row["unit_cost"]
            ),
        )
        for row in result
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


@pytest.mark.asyncio
async def test_warehouse_transfer_fifo_consumed_destination_blocks_reversal():
    """
    REAL PostgreSQL negative FIFO chronology.

    D1:
        real purchase receipt into source warehouse.

    D2:
        real internal warehouse transfer through production executor.

    D3:
        real later destination ISSUE through the same warehouse-only
        production posting boundary.

        The issue quantity is deliberately:
            all destination quantity that existed before transfer
            + 1 unit.

        Therefore, regardless of historical FIFO ordering among old
        destination lots, at least one unit from the newly-created
        transfer receipt lots MUST be consumed.

    D4:
        attempt production transfer reversal.

        Expected:
        - destination RECEIPT reversal fails because a transfer FIFO
          lot has been consumed;
        - source ISSUE reversal is never reached;
        - source FIFO quantities remain transfer-depleted;
        - no reversal WarehouseTransferEvent exists;
        - transfer source ISSUE remains POSTED;
        - transfer destination RECEIPT remains POSTED.

    Transaction proof:
        reversal attempt runs inside a caller-owned SAVEPOINT.
        After checking failure-state invariants the savepoint is rolled
        back, proving no partial destination-side reversal survives.

        Finally the complete E2E outer transaction is rolled back and
        the exact persistent database baseline must be restored.
    """

    await engine.dispose(
        close=False
    )

    baseline = await complete_baseline()

    scenario_error = None
    scenario_traceback = None

    async with engine.connect() as connection:
        transaction = await connection.begin()

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

            d4 = (
                d1
                + timedelta(days=3)
            )

            d5 = (
                d1
                + timedelta(days=4)
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
                    "Real warehouse-transfer FIFO negative E2E "
                    "requires five usable business dates "
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

            assert str(company_method) == "fifo"

            print(
                "FIFO NEGATIVE COMPANY / WAREHOUSES "
                f"source={source_warehouse_id} "
                f"destination={destination_warehouse_id} "
                "= PASS"
            )

            # ==================================================
            # B. D1 REAL PURCHASE RECEIPT
            # ==================================================

            receipt = (
                await fifo.execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=(
                        fixture["order_id"]
                    ),
                    warehouse_document_number=(
                        "WT-FIFO-NEG-PG-R-"
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

            print(
                "D1 REAL PURCHASE RECEIPT "
                f"document={receipt.warehouse_document.id} "
                "= PASS"
            )

            # ==================================================
            # C. DESTINATION QUANTITY BEFORE TRANSFER
            # ==================================================

            destination_before_rows = await rows(
                db,
                """
                SELECT
                    id,
                    remaining_quantity
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
                        destination_warehouse_id
                    ),
                },
            )

            destination_quantity_before = sum(
                (
                    Decimal(
                        row["remaining_quantity"]
                    )
                    for row
                    in destination_before_rows
                ),
                Decimal("0"),
            )

            destination_ids_before = {
                int(row["id"])
                for row
                in destination_before_rows
            }

            print(
                "DESTINATION PRE-TRANSFER FIFO "
                f"quantity={destination_quantity_before} "
                f"lots={len(destination_ids_before)} "
                "= SNAPSHOT PASS"
            )

            # ==================================================
            # D. D2 REAL TRANSFER
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
                "D2 PRODUCTION TRANSFER CREATE = PASS"
            )

            # ==================================================
            # E. LOAD TRANSFER PROVENANCE
            # ==================================================

            event_rows = await rows(
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

            assert len(event_rows) == 1
            assert (
                event_rows[0]["reversal_of_id"]
                is None
            )

            original_event_id = int(
                event_rows[0]["id"]
            )

            transfer_lines = await rows(
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
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "event_id": original_event_id,
                },
            )

            assert len(transfer_lines) == 1

            transfer_line = (
                transfer_lines[0]
            )

            source_issue_document_id = int(
                transfer_line[
                    "issue_document_id"
                ]
            )

            transfer_receipt_document_id = int(
                transfer_line[
                    "receipt_document_id"
                ]
            )

            assert (
                source_issue_document_id
                != transfer_receipt_document_id
            )

            # ==================================================
            # F. DESTINATION TRANSFER FIFO LOTS
            # ==================================================

            transfer_lots = await rows(
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
                  AND product_id = :product_id
                  AND warehouse_id = :warehouse_id
                  AND source_document_id = :document_id
                ORDER BY received_date, id
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

            assert transfer_lots

            transfer_lot_ids = {
                int(row["id"])
                for row
                in transfer_lots
            }

            assert not (
                transfer_lot_ids
                & destination_ids_before
            )

            transfer_lot_total = sum(
                (
                    Decimal(
                        row["remaining_quantity"]
                    )
                    for row in transfer_lots
                ),
                Decimal("0"),
            )

            assert (
                transfer_lot_total
                == TRANSFER_QTY
            )

            print(
                "DESTINATION TRANSFER FIFO LOTS "
                f"count={len(transfer_lots)} "
                f"quantity={transfer_lot_total} "
                "= PASS"
            )

            # ==================================================
            # G. SOURCE STATE AFTER TRANSFER
            # ==================================================

            source_after_transfer = (
                await source_fifo_snapshot(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        source_warehouse_id
                    ),
                )
            )

            assert source_after_transfer

            source_reversal_ledger_before = int(
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
                        "document_id": (
                            source_issue_document_id
                        ),
                    },
                )
            )

            assert (
                source_reversal_ledger_before
                == 0
            )

            # ==================================================
            # H. D3 LATER DESTINATION ISSUE
            # ==================================================

            #
            # We deliberately consume:
            #
            #   all FIFO quantity that existed at destination
            #   before the transfer
            #   + 1 unit
            #
            # Therefore at least one transferred FIFO unit MUST
            # be consumed regardless of historical ordering of
            # pre-existing destination lots.
            #
            later_issue_quantity = (
                destination_quantity_before
                + TRANSFER_LOT_TOUCH_QTY
            )

            assert (
                later_issue_quantity
                > Decimal("0")
            )

            assert (
                later_issue_quantity
                <= (
                    destination_quantity_before
                    + TRANSFER_QTY
                )
            )

            later_issue = Document(
                company_id=COMPANY_ID,
                accounting_rule_id=None,
                number=(
                    "WT-FIFO-NEG-ISSUE-"
                    + fixture["suffix"]
                ),
                document_type=(
                    DocumentType.ISSUE
                ),
                document_date=d3,
                status=DocumentStatus.DRAFT,
                created_by=USER_ID,
            )

            db.add(
                later_issue
            )

            await db.flush()

            assert (
                later_issue.id
                is not None
            )

            later_issue_line = DocumentLine(
                document_id=later_issue.id,
                product_id=product_id,
                warehouse_id=(
                    destination_warehouse_id
                ),
                quantity=later_issue_quantity,
                price=Decimal("0"),
            )

            db.add(
                later_issue_line
            )

            await db.flush()

            assert (
                later_issue_line.id
                is not None
            )

            await post_warehouse_transfer_document(
                db,
                document=later_issue,
                created_by=USER_ID,
            )

            await db.flush()

            later_issue_document_id = int(
                later_issue.id
            )

            later_issue_line_id = int(
                later_issue_line.id
            )

            assert (
                await document_status(
                    db,
                    later_issue_document_id,
                )
                == "posted"
            )

            print(
                "D3 LATER DESTINATION ISSUE "
                f"quantity={later_issue_quantity} "
                f"document={later_issue_document_id} "
                "= PASS"
            )

            # ==================================================
            # I. PROVE TRANSFER LOT WAS ACTUALLY CONSUMED
            # ==================================================

            later_consumptions = await rows(
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
                        later_issue_document_id
                    ),
                    "line_id": (
                        later_issue_line_id
                    ),
                },
            )

            assert later_consumptions

            consumed_transfer_rows = tuple(
                row
                for row in later_consumptions
                if int(
                    row["stock_lot_id"]
                ) in transfer_lot_ids
            )

            assert consumed_transfer_rows, (
                "Later destination ISSUE did not consume "
                "a transfer-created FIFO lot"
            )

            consumed_transfer_qty = sum(
                (
                    Decimal(
                        row["quantity"]
                    )
                    for row
                    in consumed_transfer_rows
                ),
                Decimal("0"),
            )

            assert (
                consumed_transfer_qty
                >= TRANSFER_LOT_TOUCH_QTY
            )

            transfer_lots_after_issue = await rows(
                db,
                """
                SELECT
                    id,
                    original_quantity,
                    remaining_quantity
                FROM stock_lots
                WHERE company_id = :company_id
                  AND id = ANY(:lot_ids)
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "lot_ids": list(
                        transfer_lot_ids
                    ),
                },
            )

            consumed_transfer_lot_ids = set()

            for row in (
                transfer_lots_after_issue
            ):
                original_quantity = Decimal(
                    row["original_quantity"]
                )

                remaining_quantity = Decimal(
                    row["remaining_quantity"]
                )

                if (
                    remaining_quantity
                    != original_quantity
                ):
                    consumed_transfer_lot_ids.add(
                        int(row["id"])
                    )

            assert consumed_transfer_lot_ids

            assert (
                consumed_transfer_lot_ids
                & transfer_lot_ids
            )

            print(
                "TRANSFER FIFO LOT CONSUMED "
                f"quantity={consumed_transfer_qty} "
                f"lots={sorted(consumed_transfer_lot_ids)} "
                "= EXACT PASS"
            )

            # ==================================================
            # J. D4 INDEPENDENT DESTINATION BALANCE TOP-UP
            # ==================================================
            #
            # After the later ISSUE:
            #
            #     destination aggregate balance = 49
            #
            # Reversing the original transfer RECEIPT needs a
            # StockBalance delta of -50. Without an unrelated
            # +1 receipt, WarehouseReversalHandler correctly
            # blocks first at the aggregate negative-stock guard.
            #
            # We intentionally add one NEW independent FIFO
            # receipt after the transfer lot was consumed.
            #
            # This makes aggregate StockBalance exactly sufficient
            # for reversal (-50) while preserving the historical
            # fact that an ORIGINAL TRANSFER FIFO LOT is consumed.
            #
            # Therefore reversal proceeds through StockBalance
            # validation and must fail specifically inside
            # reverse_receipt_fifo().
            #

            balance_before_top_up = Decimal(
                await scalar(
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
                        "warehouse_id": (
                            destination_warehouse_id
                        ),
                    },
                )
            )

            assert (
                balance_before_top_up
                == (
                    TRANSFER_QTY
                    - TRANSFER_LOT_TOUCH_QTY
                )
            ), (
                "Unexpected destination balance before "
                f"top-up: {balance_before_top_up}"
            )

            top_up_receipt = Document(
                company_id=COMPANY_ID,
                accounting_rule_id=None,
                number=(
                    "WT-FIFO-NEG-TOPUP-"
                    + fixture["suffix"]
                ),
                document_type=(
                    DocumentType.RECEIPT
                ),
                document_date=d4,
                status=DocumentStatus.DRAFT,
                created_by=USER_ID,
            )

            db.add(
                top_up_receipt
            )

            await db.flush()

            assert (
                top_up_receipt.id
                is not None
            )

            top_up_line = DocumentLine(
                document_id=top_up_receipt.id,
                product_id=product_id,
                warehouse_id=(
                    destination_warehouse_id
                ),
                quantity=BALANCE_TOP_UP_QTY,
                price=Decimal("1.0000"),
            )

            db.add(
                top_up_line
            )

            await db.flush()

            assert (
                top_up_line.id
                is not None
            )

            await post_warehouse_transfer_document(
                db,
                document=top_up_receipt,
                created_by=USER_ID,
            )

            await db.flush()

            top_up_document_id = int(
                top_up_receipt.id
            )

            assert (
                await document_status(
                    db,
                    top_up_document_id,
                )
                == "posted"
            )

            balance_after_top_up = Decimal(
                await scalar(
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
                        "warehouse_id": (
                            destination_warehouse_id
                        ),
                    },
                )
            )

            assert (
                balance_after_top_up
                == TRANSFER_QTY
            ), (
                "Destination balance top-up did not produce "
                f"exact reversal capacity: {balance_after_top_up}"
            )

            top_up_lots = await rows(
                db,
                """
                SELECT
                    id,
                    source_document_id,
                    original_quantity,
                    remaining_quantity
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
                        top_up_document_id
                    ),
                },
            )

            assert len(
                top_up_lots
            ) == 1

            assert (
                Decimal(
                    top_up_lots[0][
                        "remaining_quantity"
                    ]
                )
                == BALANCE_TOP_UP_QTY
            )

            #
            # Critical chronology proof:
            # the top-up does NOT "heal" the consumed transfer lot.
            #
            transfer_lots_after_top_up = await rows(
                db,
                """
                SELECT
                    id,
                    original_quantity,
                    remaining_quantity
                FROM stock_lots
                WHERE company_id = :company_id
                  AND id = ANY(:lot_ids)
                ORDER BY id
                """,
                {
                    "company_id": COMPANY_ID,
                    "lot_ids": list(
                        transfer_lot_ids
                    ),
                },
            )

            consumed_after_top_up = {
                int(row["id"])
                for row
                in transfer_lots_after_top_up
                if Decimal(
                    row["remaining_quantity"]
                )
                != Decimal(
                    row["original_quantity"]
                )
            }

            assert (
                consumed_after_top_up
                == consumed_transfer_lot_ids
            )

            print(
                "D4 INDEPENDENT BALANCE TOP-UP RECEIPT "
                f"quantity={BALANCE_TOP_UP_QTY} "
                f"aggregate_balance={balance_after_top_up} "
                "= PASS"
            )

            print(
                "TRANSFER LOT REMAINS CONSUMED "
                "AFTER UNRELATED RECEIPT "
                "= PASS"
            )

            # ==================================================
            # K. SOURCE MUST STILL BE TRANSFER-DEPLETED
            # ==================================================

            source_before_reversal_attempt = (
                await source_fifo_snapshot(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        source_warehouse_id
                    ),
                )
            )

            assert (
                source_before_reversal_attempt
                == source_after_transfer
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
                    transfer_receipt_document_id,
                )
                == "posted"
            )

            history_before_attempt = await rows(
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

            assert len(
                history_before_attempt
            ) == 1

            # ==================================================
            # L. D5 REVERSAL MUST FAIL
            # ==================================================

            #
            # Caller-owned SAVEPOINT:
            # production services intentionally own neither
            # COMMIT nor ROLLBACK.
            #
            reversal_savepoint = (
                await db.begin_nested()
            )

            try:
                with pytest.raises(
                    ValueError
                ) as exc_info:
                    await execute_warehouse_transfer_reconciliation(
                        db,
                        company_id=COMPANY_ID,
                        history_key=history_key,
                        target=None,
                        adjustment_date=d5,
                        created_by=USER_ID,
                    )

                error_message = str(
                    exc_info.value
                )

                print(
                    "EXPECTED REVERSAL ERROR = "
                    + error_message
                )

                assert (
                    "destination transfer RECEIPT "
                    "reversal failed"
                    in error_message
                )

                assert (
                    "Cannot reverse receipt document"
                    in error_message
                )

                assert (
                    "partially or fully consumed"
                    in error_message
                )

                print(
                    "D4 TRANSFER REVERSAL BLOCKED "
                    "BY CONSUMED DESTINATION FIFO LOT "
                    "= PASS"
                )

                # ==============================================
                # M. FAILURE-STATE PROOF BEFORE SAVEPOINT ROLLBACK
                # ==============================================

                source_during_failed_reversal = (
                    await source_fifo_snapshot(
                        db,
                        product_id=product_id,
                        warehouse_id=(
                            source_warehouse_id
                        ),
                    )
                )

                assert (
                    source_during_failed_reversal
                    == source_before_reversal_attempt
                ), (
                    "Source FIFO lots changed even though "
                    "destination reversal failed first"
                )

                source_reversal_ledger_during = int(
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
                            "document_id": (
                                source_issue_document_id
                            ),
                        },
                    )
                )

                assert (
                    source_reversal_ledger_during
                    == source_reversal_ledger_before
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
                        transfer_receipt_document_id,
                    )
                    == "posted"
                )

                history_during_failure = await rows(
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

                assert len(
                    history_during_failure
                ) == 1

                assert (
                    history_during_failure[0][
                        "reversal_of_id"
                    ]
                    is None
                )

                print(
                    "SOURCE ISSUE REVERSAL NOT REACHED "
                    "= PASS"
                )

                print(
                    "NO REVERSAL HISTORY EVENT "
                    "= PASS"
                )

                print(
                    "TRANSFER DOCUMENT STATUSES "
                    "REMAIN POSTED "
                    "= PASS"
                )

            finally:
                if (
                    reversal_savepoint.is_active
                ):
                    await reversal_savepoint.rollback()

            # ==================================================
            # N. CALLER ROLLBACK OF FAILED REVERSAL
            # ==================================================

            source_after_savepoint_rollback = (
                await source_fifo_snapshot(
                    db,
                    product_id=product_id,
                    warehouse_id=(
                        source_warehouse_id
                    ),
                )
            )

            assert (
                source_after_savepoint_rollback
                == source_before_reversal_attempt
            )

            transfer_lots_after_savepoint = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        original_quantity,
                        remaining_quantity
                    FROM stock_lots
                    WHERE company_id = :company_id
                      AND id = ANY(:lot_ids)
                    ORDER BY id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "lot_ids": list(
                            transfer_lot_ids
                        ),
                    },
                )
            )

            after_savepoint_by_id = {
                int(row["id"]): (
                    Decimal(
                        row[
                            "original_quantity"
                        ]
                    ),
                    Decimal(
                        row[
                            "remaining_quantity"
                        ]
                    ),
                )
                for row
                in transfer_lots_after_savepoint
            }

            assert (
                consumed_transfer_lot_ids
                <= set(
                    after_savepoint_by_id
                )
            )

            for lot_id in (
                consumed_transfer_lot_ids
            ):
                (
                    original_quantity,
                    remaining_quantity,
                ) = after_savepoint_by_id[
                    lot_id
                ]

                assert (
                    remaining_quantity
                    != original_quantity
                )

            history_after_savepoint = await rows(
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

            assert len(
                history_after_savepoint
            ) == 1

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
                    transfer_receipt_document_id,
                )
                == "posted"
            )

            assert (
                await document_status(
                    db,
                    later_issue_document_id,
                )
                == "posted"
            )

            assert (
                await document_status(
                    db,
                    top_up_document_id,
                )
                == "posted"
            )

            print(
                "FAILED REVERSAL SAVEPOINT ROLLBACK "
                "= PASS"
            )

            print(
                "CONSUMED DESTINATION LOT STATE "
                "PRESERVED AFTER FAILED REVERSAL "
                "= PASS"
            )

            print()
            print(
                "WAREHOUSE FIFO NEGATIVE POSTGRESQL "
                "CHRONOLOGY = PASS"
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
    # O. EXACT OUTER TRANSACTION ROLLBACK
    # ======================================================

    after = await complete_baseline()

    assert after == baseline, (
        "\nWarehouse Transfer FIFO negative PostgreSQL "
        "E2E rollback did not restore exact "
        "row-count baseline.\n"
        f"before={baseline}\n"
        f"after={after}"
    )

    print(
        "WAREHOUSE FIFO NEGATIVE FULL TRANSACTION "
        "ROLLBACK = PASS"
    )

    if scenario_error is not None:
        raise scenario_error.with_traceback(
            scenario_traceback
        )
