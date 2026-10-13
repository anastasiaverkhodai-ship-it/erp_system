"""Phase 13 C3-B2-C2: real PostgreSQL technical-ledger E2E.

All fixture writes are rolled back. No payroll posting.
"""

import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.database import engine as configured_engine
from app.services.payroll_deduction_remainder_persistence_service import (
    DeductionPersistenceError,
    open_persisted_remainder,
    preview_persisted_remainder,
    append_persisted_remainder_event,
)


@pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_E2E") != "1",
    reason="Set RUN_POSTGRES_E2E=1",
)
def test_real_postgresql_remainder_persistence():
    asyncio.run(_run_e2e())


async def _run_e2e():
    url = configured_engine.url

    assert url.get_backend_name() == "postgresql"
    assert url.host in ("localhost", "127.0.0.1", "::1")
    assert url.database is not None and url.database.startswith("erp_r3_copy_")

    engine = create_async_engine(
        url, poolclass=NullPool
    )

    token = uuid4().hex
    key = "c3b2-" + token
    e1 = "c3b2-a-" + token
    e2 = "c3b2-b-" + token

    try:
        async with engine.connect() as conn:
            tx = await conn.begin()

            try:
                async with AsyncSession(
                    bind=conn,
                    expire_on_commit=False,
                    autoflush=False,
                ) as db:

                    fixture = (
                        await db.execute(
                            text("""
                                SELECT ec.company_id, ec.id, u.id
                                FROM employment_contracts ec
                                CROSS JOIN users u
                                ORDER BY ec.id, u.id
                                LIMIT 1
                            """)
                        )
                    ).one_or_none()

                    assert fixture is not None, (
                        "An existing local employment contract "
                        "and user are required"
                    )

                    company, contract, actor = fixture

                    instruction = (
                        await db.execute(
                            text("""
                                INSERT INTO
                                    payroll_deduction_instructions (
                                        company_id,
                                        employment_contract_id,
                                        deduction_type,
                                        method,
                                        fixed_amount,
                                        percentage,
                                        currency_code,
                                        priority,
                                        effective_from,
                                        effective_to,
                                        request_key,
                                        source_reference,
                                        created_by,
                                        created_at
                                    )
                                VALUES (
                                    :company,
                                    :contract,
                                    'technical_e2e_only',
                                    'fixed',
                                    100.00,
                                    NULL,
                                    'UAH',
                                    1,
                                    DATE '2026-01-01',
                                    NULL,
                                    :request_key,
                                    'TEST_NOT_AUTHORIZED',
                                    :actor,
                                    now()
                                )
                                RETURNING id
                            """),
                            {
                                "company": company,
                                "contract": contract,
                                "request_key": key,
                                "actor": actor,
                            },
                        )
                    ).scalar_one()

                    opened = await open_persisted_remainder(
                        db=db,
                        company_id=company,
                        instruction_id=instruction,
                        original_amount=Decimal("100.00"),
                        currency_code="UAH",
                        actor_id=actor,
                    )

                    assert opened.original_amount == Decimal(
                        "100.00"
                    )

                    assert opened.remaining_amount == Decimal(
                        "100.00"
                    )

                    repeated = await open_persisted_remainder(
                        db=db,
                        company_id=company,
                        instruction_id=instruction,
                        original_amount=Decimal("100.00"),
                        currency_code="UAH",
                        actor_id=actor,
                    )

                    assert repeated.remainder_id == opened.remainder_id

                    print("OPENING IDEMPOTENCY = PASS")

                    with pytest.raises(
                        DeductionPersistenceError,
                        match="payload mismatch",
                    ):
                        await open_persisted_remainder(
                            db=db,
                            company_id=company,
                            instruction_id=instruction,
                            original_amount=Decimal("90.00"),
                            currency_code="UAH",
                            actor_id=actor,
                        )

                    print("CHANGED OPENING = BLOCKED")

                    first = await append_persisted_remainder_event(
                        db=db,
                        company_id=company,
                        instruction_id=instruction,
                        event_id=e1,
                        amount=Decimal("30.00"),
                        actor_id=actor,
                    )

                    assert not first.duplicate
                    assert first.preview.remaining_amount == Decimal(
                        "70.00"
                    )

                    replay = await append_persisted_remainder_event(
                        db=db,
                        company_id=company,
                        instruction_id=instruction,
                        event_id=e1,
                        amount=Decimal("30.00"),
                        actor_id=actor,
                    )

                    assert replay.duplicate
                    assert replay.applied_now == Decimal("0.00")

                    print("IDENTICAL EVENT REPLAY = PASS")

                    with pytest.raises(
                        DeductionPersistenceError,
                        match="different payload",
                    ):
                        await append_persisted_remainder_event(
                            db=db,
                            company_id=company,
                            instruction_id=instruction,
                            event_id=e1,
                            amount=Decimal("31.00"),
                            actor_id=actor,
                        )

                    print("CHANGED EVENT REPLAY = BLOCKED")

                    second = await append_persisted_remainder_event(
                        db=db,
                        company_id=company,
                        instruction_id=instruction,
                        event_id=e2,
                        amount=Decimal("20.00"),
                        actor_id=actor,
                    )

                    assert second.preview.remaining_amount == Decimal(
                        "50.00"
                    )

                    with pytest.raises(
                        DeductionPersistenceError,
                        match="exceeds",
                    ):
                        await append_persisted_remainder_event(
                            db=db,
                            company_id=company,
                            instruction_id=instruction,
                            event_id="over-" + token,
                            amount=Decimal("60.00"),
                            actor_id=actor,
                        )

                    print("OVER-ALLOCATION = BLOCKED")

                    with pytest.raises(
                        DeductionPersistenceError,
                        match="Instruction not found",
                    ):
                        await append_persisted_remainder_event(
                            db=db,
                            company_id=company + 1,
                            instruction_id=instruction,
                            event_id="cross-" + token,
                            amount=Decimal("1.00"),
                            actor_id=actor,
                        )

                    print("CROSS-COMPANY ACCESS = BLOCKED")

                    with pytest.raises(
                        DeductionPersistenceError,
                        match="company_id must be a positive integer",
                    ):
                        await append_persisted_remainder_event(
                            db=db,
                            company_id=-2147483647,
                            instruction_id=instruction,
                            event_id="invalid-company-" + token,
                            amount=Decimal("1.00"),
                            actor_id=actor,
                        )

                    print("INVALID COMPANY ID = BLOCKED")

                    preview = await preview_persisted_remainder(
                        db=db,
                        company_id=company,
                        instruction_id=instruction,
                    )

                    assert preview.original_amount == Decimal(
                        "100.00"
                    )

                    assert preview.applied_amount == Decimal(
                        "50.00"
                    )

                    assert preview.remaining_amount == Decimal(
                        "50.00"
                    )

                    assert preview.event_count == 2
                    assert not preview.payroll_posting_allowed
                    assert preview.legal_authorization == (
                        "NOT_AUTHORIZED"
                    )

                    print("PERSISTED RECONCILIATION = PASS")
                    print("PAYROLL POSTING = DISABLED")

                    # Verify the real database contains both
                    # event rows before transaction rollback.
                    count = (
                        await db.execute(
                            text("""
                                SELECT count(*)
                                FROM payroll_deduction_remainder_events
                                WHERE company_id = :company
                                  AND event_id IN (:e1, :e2)
                            """),
                            {
                                "company": company,
                                "e1": e1,
                                "e2": e2,
                            },
                        )
                    ).scalar_one()

                    assert count == 2

                    print("POSTGRESQL EVENT INSERTS = 2")

                    # =================================================
                    # REAL POSTGRESQL TRANSACTION CONCURRENCY
                    #
                    # No previously committed deduction instruction
                    # is required.
                    #
                    # The service already acquired an advisory
                    # transaction lock on (company, instruction).
                    #
                    # A separate DB connection must NOT be able
                    # to acquire the same advisory lock.
                    #
                    # The test creates no permanent financial data.
                    # =================================================

                    print(
                        "TESTING TRANSACTION ADVISORY LOCK..."
                    )

                    async with engine.connect() as competitor:

                        competing_tx = await competitor.begin()

                        try:
                            # Do not block indefinitely.
                            #
                            # pg_try_advisory_xact_lock returns:
                            # True  = lock acquired
                            # False = held by another transaction

                            competing_result = (
                                await competitor.execute(
                                    text("""
                                        SELECT
                                            pg_try_advisory_xact_lock(
                                                :company,
                                                :instruction
                                            )
                                    """),
                                    {
                                        "company": company,
                                        "instruction": instruction,
                                    },
                                )
                            ).scalar_one()

                            assert competing_result is False, (
                                "Concurrent transaction acquired "
                                "an already-held advisory lock"
                            )

                            print(
                                "COMPETING TRANSACTION = BLOCKED"
                            )

                            # An unrelated lock scope must remain
                            # available, proving company/instruction
                            # isolation in the lock namespace.

                            independent_result = (
                                await competitor.execute(
                                    text("""
                                        SELECT
                                            pg_try_advisory_xact_lock(
                                                :company,
                                                :instruction
                                            )
                                    """),
                                    {
                                        "company": company,
                                        "instruction": instruction + 1,
                                    },
                                )
                            ).scalar_one()

                            assert independent_result is True

                            print(
                                "UNRELATED INSTRUCTION LOCK "
                                "= INDEPENDENT"
                            )

                        finally:
                            if competing_tx.is_active:
                                await competing_tx.rollback()

                    print("REAL POSTGRESQL CONCURRENCY = PASS")
            finally:
                if tx.is_active:
                    await tx.rollback()

        # Fresh connection: test fixture must not persist.
        async with engine.connect() as verification:
            instruction_count = (
                await verification.execute(
                    text("""
                        SELECT count(*)
                        FROM payroll_deduction_instructions
                        WHERE request_key = :key
                    """),
                    {"key": key},
                )
            ).scalar_one()

            event_count = (
                await verification.execute(
                    text("""
                        SELECT count(*)
                        FROM payroll_deduction_remainder_events
                        WHERE event_id IN (:e1, :e2)
                    """),
                    {"e1": e1, "e2": e2},
                )
            ).scalar_one()

            assert instruction_count == 0
            assert event_count == 0

            await verification.rollback()

        print("ROLLBACK ZERO-LEAK = PASS")

    finally:
        await engine.dispose()
