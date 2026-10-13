"""Phase 13 C4-C2: real PostgreSQL remittance persistence E2E.

All fixture data is rolled back.
No bank payments, payroll posting or legal authorization.
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

from app.services.payroll_deduction_remittance_persistence_service import (
    RemittancePersistenceError,
    open_persisted_remittance_plan,
    append_persisted_remittance_event,
    read_persisted_remittance_balance,
)
from app.services.payroll_deduction_remittance_rules import (
    RemittanceRuleError,
)


INSTRUCTION_SQL = "\n                                INSERT INTO\n                                    payroll_deduction_instructions (\n                                        company_id,\n                                        employment_contract_id,\n                                        deduction_type,\n                                        method,\n                                        fixed_amount,\n                                        percentage,\n                                        currency_code,\n                                        priority,\n                                        effective_from,\n                                        effective_to,\n                                        request_key,\n                                        source_reference,\n                                        created_by,\n                                        created_at\n                                    )\n                                VALUES (\n                                    :company,\n                                    :contract,\n                                    'technical_e2e_only',\n                                    'fixed',\n                                    100.00,\n                                    NULL,\n                                    'UAH',\n                                    1,\n                                    DATE '2026-01-01',\n                                    NULL,\n                                    :request_key,\n                                    'TEST_NOT_AUTHORIZED',\n                                    :actor,\n                                    now()\n                                )\n                                RETURNING id\n                            "


@pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_E2E") != "1",
    reason="Set RUN_POSTGRES_E2E=1",
)
def test_real_postgresql_remittance_persistence():
    asyncio.run(_run_e2e())


async def _run_e2e():

    engine = create_async_engine(
        configured_engine.url,
        poolclass=NullPool,
        echo=False,
    )

    token = uuid4().hex[:16]

    key1 = "c4-remittance-a-" + token
    key2 = "c4-remittance-b-" + token

    e1 = "c4-event-1-" + token
    e2 = "c4-event-2-" + token

    async def insert_instruction(db, company, contract, actor, key):
        return (
            await db.execute(
                text(INSTRUCTION_SQL),
                {
                    "company": company,
                    "contract": contract,
                    "request_key": key,
                    "actor": actor,
                },
            )
        ).scalar_one()

    async def count_rows(db, table, company, plan_id):
        if table == "plans":
            sql = """
                SELECT COUNT(*)
                FROM payroll_deduction_remittance_plans
                WHERE company_id = :company
                  AND id = :plan
            """
        else:
            sql = """
                SELECT COUNT(*)
                FROM payroll_deduction_remittance_events
                WHERE company_id = :company
                  AND plan_id = :plan
            """

        return (
            await db.execute(
                text(sql),
                {"company": company, "plan": plan_id},
            )
        ).scalar_one()

    try:

        async with engine.connect() as conn:
            tx = await conn.begin()

            try:

                async with AsyncSession(
                    bind=conn,
                    expire_on_commit=False,
                ) as db:

                    fixture = (
                        await db.execute(
                            text("""
                                SELECT
                                    ec.company_id,
                                    ec.id,
                                    u.id
                                FROM employment_contracts ec
                                CROSS JOIN users u
                                ORDER BY ec.id, u.id
                                LIMIT 1
                            """)
                        )
                    ).one_or_none()

                    assert fixture is not None, (
                        "Existing employment contract and user "
                        "required for PostgreSQL E2E"
                    )

                    company, contract, actor = fixture

                    instruction1 = await insert_instruction(
                        db, company, contract, actor, key1
                    )

                    instruction2 = await insert_instruction(
                        db, company, contract, actor, key2
                    )

                    # ----------------------------------------
                    # OPEN TECHNICAL PLAN
                    # ----------------------------------------

                    plan, balance, duplicate = (
                        await open_persisted_remittance_plan(
                            db=db,
                            company_id=company,
                            instruction_id=instruction1,
                            beneficiary_reference="TEST-BENEFICIARY",
                            currency_code="UAH",
                            available_amount=Decimal("100.00"),
                            created_by=actor,
                        )
                    )

                    assert duplicate is False
                    assert balance.remaining_amount == Decimal("100.00")
                    assert balance.allocated_amount == Decimal("0.00")

                    plan_id = plan.id

                    assert await count_rows(
                        db, "plans", company, plan_id
                    ) == 1

                    print("REAL POSTGRESQL PLAN OPENING = PASS")

                    # ----------------------------------------
                    # EXACT OPENING REPLAY
                    # ----------------------------------------

                    repeated_plan, repeated_balance, duplicate = (
                        await open_persisted_remittance_plan(
                            db=db,
                            company_id=company,
                            instruction_id=instruction1,
                            beneficiary_reference="TEST-BENEFICIARY",
                            currency_code="UAH",
                            available_amount=Decimal("100.00"),
                            created_by=actor,
                        )
                    )

                    assert duplicate is True
                    assert repeated_plan.id == plan_id
                    assert repeated_balance == balance

                    assert await count_rows(
                        db, "plans", company, plan_id
                    ) == 1

                    print("OPENING IDEMPOTENCY = PASS")

                    # ----------------------------------------
                    # CHANGED OPENING REPLAY
                    # ----------------------------------------

                    with pytest.raises(
                        RemittancePersistenceError,
                        match="Changed remittance opening replay",
                    ):
                        await open_persisted_remittance_plan(
                            db=db,
                            company_id=company,
                            instruction_id=instruction1,
                            beneficiary_reference="TEST-BENEFICIARY",
                            currency_code="UAH",
                            available_amount=Decimal("99.00"),
                            created_by=actor,
                        )

                    print("CHANGED OPENING REPLAY = BLOCKED")

                    # ----------------------------------------
                    # SOURCE CURRENCY BINDING
                    # ----------------------------------------

                    with pytest.raises(
                        RemittancePersistenceError,
                        match="Instruction currency mismatch",
                    ):
                        await open_persisted_remittance_plan(
                            db=db,
                            company_id=company,
                            instruction_id=instruction1,
                            beneficiary_reference="TEST-BENEFICIARY",
                            currency_code="EUR",
                            available_amount=Decimal("100.00"),
                            created_by=actor,
                        )

                    print("SOURCE CURRENCY MISMATCH = BLOCKED")

                    # ----------------------------------------
                    # FIRST PARTIAL ALLOCATION
                    # ----------------------------------------

                    first = await append_persisted_remittance_event(
                        db=db,
                        company_id=company,
                        instruction_id=instruction1,
                        event_id=e1,
                        amount=Decimal("30.00"),
                        created_by=actor,
                    )

                    assert first.duplicate is False
                    assert first.applied_now == Decimal("30.00")
                    assert first.balance.remaining_amount == Decimal("70.00")

                    print("FIRST PARTIAL EVENT = PASS")

                    # ----------------------------------------
                    # IDENTICAL EVENT REPLAY
                    # ----------------------------------------

                    replay = await append_persisted_remittance_event(
                        db=db,
                        company_id=company,
                        instruction_id=instruction1,
                        event_id=e1,
                        amount=Decimal("30.00"),
                        created_by=actor,
                    )

                    assert replay.duplicate is True
                    assert replay.applied_now == Decimal("0.00")

                    assert await count_rows(
                        db, "events", company, plan_id
                    ) == 1

                    print("IDENTICAL EVENT REPLAY = PASS")

                    # ----------------------------------------
                    # CHANGED EVENT REPLAY
                    # ----------------------------------------

                    with pytest.raises(
                        RemittanceRuleError,
                        match="different payload",
                    ):
                        await append_persisted_remittance_event(
                            db=db,
                            company_id=company,
                            instruction_id=instruction1,
                            event_id=e1,
                            amount=Decimal("31.00"),
                            created_by=actor,
                        )

                    print("CHANGED EVENT REPLAY = BLOCKED")

                    # ----------------------------------------
                    # SECOND PARTIAL ALLOCATION
                    # ----------------------------------------

                    second = await append_persisted_remittance_event(
                        db=db,
                        company_id=company,
                        instruction_id=instruction1,
                        event_id=e2,
                        amount=Decimal("20.00"),
                        created_by=actor,
                    )

                    assert second.duplicate is False
                    assert second.balance.allocated_amount == Decimal("50.00")
                    assert second.balance.remaining_amount == Decimal("50.00")

                    print("SECOND PARTIAL EVENT = PASS")

                    # ----------------------------------------
                    # OVER-ALLOCATION
                    # ----------------------------------------

                    with pytest.raises(
                        RemittanceRuleError,
                        match="exceeds available amount",
                    ):
                        await append_persisted_remittance_event(
                            db=db,
                            company_id=company,
                            instruction_id=instruction1,
                            event_id="c4-over-" + token,
                            amount=Decimal("50.01"),
                            created_by=actor,
                        )

                    print("OVER-ALLOCATION = BLOCKED")

                    # ----------------------------------------
                    # COMPANY ISOLATION
                    # ----------------------------------------

                    with pytest.raises(
                        RemittancePersistenceError,
                        match="Remittance plan not found in company",
                    ):
                        await append_persisted_remittance_event(
                            db=db,
                            company_id=company + 1,
                            instruction_id=instruction1,
                            event_id="c4-cross-" + token,
                            amount=Decimal("1.00"),
                            created_by=actor,
                        )

                    print("COMPANY ISOLATION = PASS")

                    # ----------------------------------------
                    # SECOND PLAN + CROSS-PLAN EVENT COLLISION
                    # ----------------------------------------

                    other_plan, other_balance, other_duplicate = (
                        await open_persisted_remittance_plan(
                            db=db,
                            company_id=company,
                            instruction_id=instruction2,
                            beneficiary_reference="OTHER-BENEFICIARY",
                            currency_code="UAH",
                            available_amount=Decimal("100.00"),
                            created_by=actor,
                        )
                    )

                    assert other_duplicate is False
                    assert other_plan.id != plan_id

                    with pytest.raises(
                        RemittancePersistenceError,
                        match="already belongs to another plan",
                    ):
                        await append_persisted_remittance_event(
                            db=db,
                            company_id=company,
                            instruction_id=instruction2,
                            event_id=e1,
                            amount=Decimal("30.00"),
                            created_by=actor,
                        )

                    print("CROSS-PLAN EVENT COLLISION = BLOCKED")

                    # ----------------------------------------
                    # INDEPENDENT POSTGRESQL BALANCE
                    # ----------------------------------------

                    persisted_balance = (
                        await read_persisted_remittance_balance(
                            db=db,
                            company_id=company,
                            instruction_id=instruction1,
                        )
                    )

                    assert (
                        persisted_balance.allocated_amount
                        == Decimal("50.00")
                    )

                    assert (
                        persisted_balance.remaining_amount
                        == Decimal("50.00")
                    )

                    assert persisted_balance.event_count == 2
                    assert persisted_balance.reconciled is True

                    sql_total = (
                        await db.execute(
                            text("""
                                SELECT
                                    COUNT(*) AS n,
                                    COALESCE(SUM(amount), 0) AS total
                                FROM payroll_deduction_remittance_events
                                WHERE company_id = :company
                                  AND plan_id = :plan
                            """),
                            {
                                "company": company,
                                "plan": plan_id,
                            },
                        )
                    ).one()

                    assert sql_total.n == 2
                    assert sql_total.total == Decimal("50.00")

                    print("INDEPENDENT SQL EVENT RECONCILIATION = PASS")

                    # ----------------------------------------
                    # ACTUAL POSTGRESQL IMMUTABILITY
                    # ----------------------------------------

                    async def expect_immutable(sql, label):
                        savepoint = await db.begin_nested()

                        try:
                            await db.execute(text(sql))

                        except DBAPIError as exc:
                            await savepoint.rollback()

                            assert "immutable" in str(exc).lower()

                            print(label, "= BLOCKED")

                        else:
                            await savepoint.rollback()

                            raise AssertionError(
                                label + " unexpectedly succeeded"
                            )

                    await expect_immutable(
                        f"""
                            UPDATE payroll_deduction_remittance_plans
                            SET available_amount = 200.00
                            WHERE id = {int(plan_id)}
                        """,
                        "PLAN UPDATE",
                    )

                    await expect_immutable(
                        f"""
                            DELETE FROM payroll_deduction_remittance_plans
                            WHERE id = {int(plan_id)}
                        """,
                        "PLAN DELETE",
                    )

                    await expect_immutable(
                        f"""
                            UPDATE payroll_deduction_remittance_events
                            SET amount = 99.00
                            WHERE company_id = {int(company)}
                              AND plan_id = {int(plan_id)}
                        """,
                        "EVENT UPDATE",
                    )

                    await expect_immutable(
                        f"""
                            DELETE FROM payroll_deduction_remittance_events
                            WHERE company_id = {int(company)}
                              AND plan_id = {int(plan_id)}
                        """,
                        "EVENT DELETE",
                    )

                    print("REAL TABLE IMMUTABILITY = PASS")

                    # ----------------------------------------
                    # REAL TRANSACTION CONCURRENCY
                    # ----------------------------------------

                    async with engine.connect() as competitor:
                        competing_tx = await competitor.begin()

                        try:

                            locked = (
                                await competitor.execute(
                                    text("""
                                        SELECT pg_try_advisory_xact_lock(
                                            :company,
                                            :instruction
                                        )
                                    """),
                                    {
                                        "company": company,
                                        "instruction": instruction1,
                                    },
                                )
                            ).scalar_one()

                            assert locked is False

                            unrelated = (
                                await competitor.execute(
                                    text("""
                                        SELECT pg_try_advisory_xact_lock(
                                            :company,
                                            :instruction
                                        )
                                    """),
                                    {
                                        "company": company,
                                        "instruction": instruction1 + 100000,
                                    },
                                )
                            ).scalar_one()

                            assert unrelated is True

                        finally:
                            await competing_tx.rollback()

                    print("COMPETING TRANSACTION = BLOCKED")
                    print("UNRELATED LOCK = INDEPENDENT")
                    print("POSTGRESQL CONCURRENCY = PASS")

                    # ----------------------------------------
                    # NO PAYMENT OR PAYROLL AUTHORIZATION
                    # ----------------------------------------

                    assert (
                        persisted_balance.payment_execution_allowed
                        is False
                    )

                    assert (
                        persisted_balance.payroll_posting_allowed
                        is False
                    )

                    assert (
                        persisted_balance.legal_authorization
                        == "NOT_AUTHORIZED"
                    )

                    print("PAYMENT EXECUTION = DISABLED")
                    print("PAYROLL POSTING = DISABLED")
                    print("LEGAL AUTHORIZATION = NOT_AUTHORIZED")

            finally:
                if tx.is_active:
                    await tx.rollback()

        # --------------------------------------------
        # FRESH CONNECTION: ZERO-LEAK PROOF
        # --------------------------------------------

        async with engine.connect() as verification:

            remaining_instructions = (
                await verification.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_deduction_instructions
                        WHERE request_key IN (:key1, :key2)
                    """),
                    {"key1": key1, "key2": key2},
                )
            ).scalar_one()

            remaining_plans = (
                await verification.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_deduction_remittance_plans p
                        JOIN payroll_deduction_instructions i
                          ON i.company_id = p.company_id
                         AND i.id = p.instruction_id
                        WHERE i.request_key IN (:key1, :key2)
                    """),
                    {"key1": key1, "key2": key2},
                )
            ).scalar_one()

            remaining_events = (
                await verification.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_deduction_remittance_events
                        WHERE event_id IN (:e1, :e2)
                    """),
                    {"e1": e1, "e2": e2},
                )
            ).scalar_one()

            assert remaining_instructions == 0
            assert remaining_plans == 0
            assert remaining_events == 0

            await verification.rollback()

        print("ROLLBACK ZERO-LEAK = PASS")
        print("REAL POSTGRESQL E2E = PASS")

    finally:
        await engine.dispose()
