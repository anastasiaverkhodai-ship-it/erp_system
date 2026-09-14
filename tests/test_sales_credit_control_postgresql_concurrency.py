from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.core.database import (
    AsyncSessionLocal,
    engine,
)
from app.models.trade_document import TradeDocument
from app.services import sales_credit_control_service as credit
from app.services.trade_document_types import (
    TradeDocumentStatus,
)


RUN_POSTGRES_E2E = (
    os.getenv("RUN_POSTGRES_E2E", "").strip() == "1"
)


@pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 to run real PostgreSQL "
        "Sales credit concurrency test"
    ),
)
@pytest.mark.asyncio
async def test_sales_credit_counterparty_lock_serializes_orders_postgresql():
    """
    Real PostgreSQL concurrency proof.

    Two independent transactions confirm economic credit
    commitments for two different Sales Orders belonging
    to the same customer.

    Limit = 100
    Order A = 60
    Order B = 60

    Required chronology:

        TX-A
            lock Counterparty
            credit check -> 60 <= 100
            persist Order A as CONFIRMED
            HOLD lock

        TX-B
            attempts same Counterparty FOR UPDATE
            MUST block

        TX-A COMMIT

        TX-B resumes
            now sees confirmed Order A = 60
            exposure after Order B = 120
            -> SalesCreditLimitExceededError

    The test owns setup/cleanup transactions. Production
    credit service remains caller-owned and never commits.
    """

    await engine.dispose(close=False)

    token = uuid4().hex[:12]
    counterparty_name = f"B5-CREDIT-{token}"
    order_a_number = f"B5-CREDIT-A-{token}"
    order_b_number = f"B5-CREDIT-B-{token}"

    counterparty_id = None
    order_a_id = None
    order_b_id = None

    first_has_lock = asyncio.Event()
    second_attempting_lock = asyncio.Event()
    release_first = asyncio.Event()

    async def cleanup_fixture():
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    """
                    DELETE FROM trade_document_lines
                    WHERE trade_document_id IN (
                        SELECT id
                        FROM trade_documents
                        WHERE number IN (
                            :order_a_number,
                            :order_b_number
                        )
                    )
                    """
                ),
                {
                    "order_a_number": order_a_number,
                    "order_b_number": order_b_number,
                },
            )

            await db.execute(
                text(
                    """
                    DELETE FROM trade_documents
                    WHERE number IN (
                        :order_a_number,
                        :order_b_number
                    )
                    """
                ),
                {
                    "order_a_number": order_a_number,
                    "order_b_number": order_b_number,
                },
            )

            await db.execute(
                text(
                    """
                    DELETE FROM counterparties
                    WHERE name = :counterparty_name
                    """
                ),
                {
                    "counterparty_name": counterparty_name,
                },
            )

            await db.commit()

    async def load_order(db, order_id: int):
        return (
            await db.execute(
                select(TradeDocument)
                .options(
                    selectinload(
                        TradeDocument.lines
                    )
                )
                .where(
                    TradeDocument.id == order_id
                )
            )
        ).scalar_one()

    async def first_transaction():
        async with AsyncSessionLocal() as db:
            document = await load_order(
                db,
                int(order_a_id),
            )

            decision = (
                await credit.enforce_sales_credit_limit(
                    db,
                    document=document,
                )
            )

            assert (
                decision.counterparty_exposure.exposure_after
                == Decimal("60.00")
            )

            document.status = (
                TradeDocumentStatus.CONFIRMED
            )
            document.confirmed_at = datetime.now(
                timezone.utc
            )

            await db.flush()

            # Counterparty FOR UPDATE remains held here.
            first_has_lock.set()

            await asyncio.wait_for(
                release_first.wait(),
                timeout=5,
            )

            await db.commit()

    async def second_transaction():
        await asyncio.wait_for(
            first_has_lock.wait(),
            timeout=5,
        )

        async with AsyncSessionLocal() as db:
            document = await load_order(
                db,
                int(order_b_id),
            )

            second_attempting_lock.set()

            with pytest.raises(
                credit.SalesCreditLimitExceededError
            ):
                await credit.enforce_sales_credit_limit(
                    db,
                    document=document,
                )

            await db.rollback()

    try:
        # -------------------------------------------------
        # A. COMMITTED SETUP
        # -------------------------------------------------
        async with AsyncSessionLocal() as setup:
            assert (
                setup.bind.dialect.name
                == "postgresql"
            )

            base = (
                await setup.execute(
                    text(
                        """
                        SELECT
                            c.id AS company_id,
                            u.id AS user_id,
                            p.id AS product_id
                        FROM companies c
                        CROSS JOIN LATERAL (
                            SELECT id
                            FROM users
                            ORDER BY id
                            LIMIT 1
                        ) u
                        CROSS JOIN LATERAL (
                            SELECT id
                            FROM products
                            WHERE company_id = c.id
                              AND is_active IS TRUE
                            ORDER BY id
                            LIMIT 1
                        ) p
                        WHERE c.is_active IS TRUE
                        ORDER BY c.id
                        LIMIT 1
                        """
                    )
                )
            ).mappings().one_or_none()

            if base is None:
                pytest.fail(
                    "Real PostgreSQL credit concurrency "
                    "fixture requires one active company, "
                    "one user and one active product"
                )

            company_id = int(
                base["company_id"]
            )
            user_id = int(
                base["user_id"]
            )
            product_id = int(
                base["product_id"]
            )

            counterparty_id = int(
                (
                    await setup.execute(
                        text(
                            """
                            INSERT INTO counterparties (
                                company_id,
                                name,
                                counterparty_type,
                                default_currency_code,
                                payment_term_days,
                                credit_limit,
                                is_active
                            )
                            VALUES (
                                :company_id,
                                :name,
                                'customer',
                                'UAH',
                                0,
                                100.00,
                                TRUE
                            )
                            RETURNING id
                            """
                        ),
                        {
                            "company_id": company_id,
                            "name": counterparty_name,
                        },
                    )
                ).scalar_one()
            )

            async def insert_order(number: str):
                document_id = int(
                    (
                        await setup.execute(
                            text(
                                """
                                INSERT INTO trade_documents (
                                    company_id,
                                    counterparty_id,
                                    contract_id,
                                    number,
                                    direction,
                                    kind,
                                    status,
                                    document_date,
                                    currency_code,
                                    payment_term_days,
                                    created_by
                                )
                                VALUES (
                                    :company_id,
                                    :counterparty_id,
                                    NULL,
                                    :number,
                                    'sale',
                                    'order',
                                    'draft',
                                    :document_date,
                                    'UAH',
                                    0,
                                    :created_by
                                )
                                RETURNING id
                                """
                            ),
                            {
                                "company_id": company_id,
                                "counterparty_id": (
                                    counterparty_id
                                ),
                                "number": number,
                                "document_date": date(
                                    2026,
                                    9,
                                    14,
                                ),
                                "created_by": user_id,
                            },
                        )
                    ).scalar_one()
                )

                await setup.execute(
                    text(
                        """
                        INSERT INTO trade_document_lines (
                            company_id,
                            trade_document_id,
                            line_number,
                            product_id,
                            warehouse_id,
                            quantity,
                            unit_price,
                            tax_rate_code,
                            tax_recognition_method,
                            tax_price_mode
                        )
                        VALUES (
                            :company_id,
                            :document_id,
                            1,
                            :product_id,
                            NULL,
                            1.0000,
                            60.0000,
                            NULL,
                            NULL,
                            NULL
                        )
                        """
                    ),
                    {
                        "company_id": company_id,
                        "document_id": document_id,
                        "product_id": product_id,
                    },
                )

                return document_id

            order_a_id = await insert_order(
                order_a_number
            )
            order_b_id = await insert_order(
                order_b_number
            )

            await setup.commit()

        # -------------------------------------------------
        # B. REAL CONCURRENT TRANSACTIONS
        # -------------------------------------------------
        task_a = asyncio.create_task(
            first_transaction()
        )

        await asyncio.wait_for(
            first_has_lock.wait(),
            timeout=5,
        )

        task_b = asyncio.create_task(
            second_transaction()
        )

        await asyncio.wait_for(
            second_attempting_lock.wait(),
            timeout=5,
        )

        # TX-B has started the credit check while TX-A still
        # owns the Counterparty row lock. It must not finish.
        await asyncio.sleep(0.20)

        assert not task_b.done(), (
            "Second credit check did not block on "
            "Counterparty FOR UPDATE"
        )

        print(
            "REAL PG CREDIT LOCK BLOCKING = PASS"
        )

        release_first.set()

        await asyncio.wait_for(
            task_a,
            timeout=5,
        )

        await asyncio.wait_for(
            task_b,
            timeout=5,
        )

        print(
            "REAL PG CREDIT SERIALIZATION = PASS"
        )

        # -------------------------------------------------
        # C. INDEPENDENT POST-CONCURRENCY PROOF
        # -------------------------------------------------
        async with AsyncSessionLocal() as verify:
            rows = (
                await verify.execute(
                    text(
                        """
                        SELECT
                            number,
                            status
                        FROM trade_documents
                        WHERE id IN (
                            :order_a_id,
                            :order_b_id
                        )
                        ORDER BY number
                        """
                    ),
                    {
                        "order_a_id": order_a_id,
                        "order_b_id": order_b_id,
                    },
                )
            ).mappings().all()

            status_by_number = {
                row["number"]: row["status"]
                for row in rows
            }

            assert (
                status_by_number[
                    order_a_number
                ]
                == "confirmed"
            )

            assert (
                status_by_number[
                    order_b_number
                ]
                == "draft"
            )

            print(
                "ORDER A = CONFIRMED"
            )
            print(
                "ORDER B = DRAFT / CREDIT REJECTED"
            )

    finally:
        release_first.set()

        await cleanup_fixture()

    # -----------------------------------------------------
    # D. INDEPENDENT ZERO-RESIDUAL PROOF
    # -----------------------------------------------------
    async with AsyncSessionLocal() as verify:
        residual_counterparty = int(
            (
                await verify.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM counterparties
                        WHERE name = :name
                        """
                    ),
                    {
                        "name": counterparty_name,
                    },
                )
            ).scalar_one()
        )

        residual_orders = int(
            (
                await verify.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM trade_documents
                        WHERE number IN (
                            :order_a_number,
                            :order_b_number
                        )
                        """
                    ),
                    {
                        "order_a_number": order_a_number,
                        "order_b_number": order_b_number,
                    },
                )
            ).scalar_one()
        )

        assert residual_counterparty == 0
        assert residual_orders == 0

        print(
            "REAL PG CREDIT CLEANUP = "
            "ZERO RESIDUAL = PASS"
        )
