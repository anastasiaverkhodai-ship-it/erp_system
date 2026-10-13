"""Phase 13 C4-D3: real PostgreSQL three-source reconciliation.

All test business records are inserted inside a rollback-only
transaction.

This is technical reconciliation, not confirmation of:
- funds withheld from payroll;
- bank payments;
- payment authorization;
- payroll posting.
"""

import asyncio
import os
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.database import engine as configured_engine

from app.services.payroll_deduction_remittance_reconciliation import (
    InstructionEvidence,
    RemainderEvidence,
    RemittanceEvidence,
    RemittanceReconciliationError,
    reconcile_remittance_sources,
)

from app.services.payroll_deduction_remittance_persistence_service import (
    open_persisted_remittance_plan,
    append_persisted_remittance_event,
)


INSTRUCTION_SQL = "\n                                INSERT INTO\n                                    payroll_deduction_instructions (\n                                        company_id,\n                                        employment_contract_id,\n                                        deduction_type,\n                                        method,\n                                        fixed_amount,\n                                        percentage,\n                                        currency_code,\n                                        priority,\n                                        effective_from,\n                                        effective_to,\n                                        request_key,\n                                        source_reference,\n                                        created_by,\n                                        created_at\n                                    )\n                                VALUES (\n                                    :company,\n                                    :contract,\n                                    'technical_e2e_only',\n                                    'fixed',\n                                    100.00,\n                                    NULL,\n                                    'UAH',\n                                    1,\n                                    DATE '2026-01-01',\n                                    NULL,\n                                    :request_key,\n                                    'TEST_NOT_AUTHORIZED',\n                                    :actor,\n                                    now()\n                                )\n                                RETURNING id\n                            "


@pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_E2E") != "1",
    reason="Set RUN_POSTGRES_E2E=1",
)
def test_real_postgresql_three_source_remittance_reconciliation():
    asyncio.run(_run_e2e())


async def _run_e2e():

    engine = create_async_engine(
        configured_engine.url,
        poolclass=NullPool,
        echo=False,
    )

    token = uuid4().hex[:16]

    request_key = "c4-d3-instruction-" + token

    remainder_event_1 = "c4-d3-remainder-a-" + token
    remainder_event_2 = "c4-d3-remainder-b-" + token

    remittance_event_1 = "c4-d3-remittance-a-" + token
    remittance_event_2 = "c4-d3-remittance-b-" + token

    company = None
    instruction_id = None
    remainder_id = None
    plan_id = None

    try:

        async with engine.connect() as conn:

            tx = await conn.begin()

            try:

                async with AsyncSession(
                    bind=conn,
                    expire_on_commit=False,
                ) as db:

                    # ========================================
                    # 1. EXISTING LOCAL FIXTURE REFERENCES
                    # ========================================

                    fixture = (
                        await db.execute(
                            text("""
                                SELECT
                                    ec.company_id,
                                    ec.id,
                                    u.id
                                FROM employment_contracts AS ec
                                CROSS JOIN users AS u
                                ORDER BY ec.id, u.id
                                LIMIT 1
                            """)
                        )
                    ).one_or_none()

                    assert fixture is not None, (
                        "Existing employment contract and user "
                        "required for isolated PostgreSQL E2E"
                    )

                    company, contract, actor = fixture

                    # ========================================
                    # 2. SOURCE INSTRUCTION
                    # ========================================

                    instruction_id = (
                        await db.execute(
                            text(INSTRUCTION_SQL),
                            {
                                "company": company,
                                "contract": contract,
                                "request_key": request_key,
                                "actor": actor,
                            },
                        )
                    ).scalar_one()

                    source_row = (
                        await db.execute(
                            text("""
                                SELECT
                                    company_id,
                                    id,
                                    fixed_amount,
                                    currency_code
                                FROM payroll_deduction_instructions
                                WHERE company_id = :company
                                  AND id = :instruction
                            """),
                            {
                                "company": company,
                                "instruction": instruction_id,
                            },
                        )
                    ).mappings().one()

                    assert (
                        source_row["fixed_amount"]
                        == Decimal("100.00")
                    )

                    assert source_row["currency_code"] == "UAH"

                    print("REAL INSTRUCTION SOURCE = PASS")

                    # ========================================
                    # 3. TECHNICAL REMAINDER OPENING
                    #
                    # This is an independent technical ledger.
                    # It is not proof of withheld funds.
                    # ========================================

                    remainder_id = (
                        await db.execute(
                            text("""
                                INSERT INTO
                                    payroll_deduction_remainders (
                                        company_id,
                                        instruction_id,
                                        original_amount,
                                        currency_code,
                                        created_by,
                                        created_at
                                    )
                                VALUES (
                                    :company,
                                    :instruction,
                                    100.00,
                                    'UAH',
                                    :actor,
                                    now()
                                )
                                RETURNING id
                            """),
                            {
                                "company": company,
                                "instruction": instruction_id,
                                "actor": actor,
                            },
                        )
                    ).scalar_one()

                    # Independently insert two remainder events.
                    #
                    # 15 + 25 = 40.
                    # Remaining = 60.
                    #
                    # These events are technical test evidence,
                    # NOT actual payroll withholding.

                    for event_id, amount in (
                        (remainder_event_1, Decimal("15.00")),
                        (remainder_event_2, Decimal("25.00")),
                    ):

                        await db.execute(
                            text("""
                                INSERT INTO
                                    payroll_deduction_remainder_events (
                                        company_id,
                                        remainder_id,
                                        event_id,
                                        amount,
                                        created_by,
                                        created_at
                                    )
                                VALUES (
                                    :company,
                                    :remainder,
                                    :event,
                                    :amount,
                                    :actor,
                                    now()
                                )
                            """),
                            {
                                "company": company,
                                "remainder": remainder_id,
                                "event": event_id,
                                "amount": amount,
                                "actor": actor,
                            },
                        )

                    print("REAL REMAINDER LEDGER = CREATED")

                    # ========================================
                    # 4. TECHNICAL REMITTANCE PLAN
                    #
                    # Uses the actual C4-C production service.
                    # ========================================

                    plan, initial_balance, repeated = (
                        await open_persisted_remittance_plan(
                            db=db,
                            company_id=company,
                            instruction_id=instruction_id,
                            beneficiary_reference="C4-D3-TEST-BENEFICIARY",
                            currency_code="UAH",
                            available_amount=Decimal("80.00"),
                            created_by=actor,
                        )
                    )

                    assert repeated is False

                    plan_id = plan.id

                    # 10 + 20 = 30.
                    # Remaining = 50.

                    for event_id, amount in (
                        (remittance_event_1, Decimal("10.00")),
                        (remittance_event_2, Decimal("20.00")),
                    ):

                        application = (
                            await append_persisted_remittance_event(
                                db=db,
                                company_id=company,
                                instruction_id=instruction_id,
                                event_id=event_id,
                                amount=amount,
                                created_by=actor,
                            )
                        )

                        assert application.duplicate is False

                    print("REAL REMITTANCE LEDGER = CREATED")

                    # ========================================
                    # 5. INDEPENDENT POSTGRESQL AGGREGATION
                    #
                    # SQL reads each ledger independently.
                    # It does not trust cached application
                    # balances or the arguments passed to
                    # the previous write operations.
                    # ========================================

                    remainder_row = (
                        await db.execute(
                            text("""
                                SELECT
                                    r.company_id,
                                    r.instruction_id,
                                    r.original_amount,
                                    r.currency_code,
                                    COUNT(e.id) AS event_count,
                                    COALESCE(
                                        SUM(e.amount),
                                        0
                                    ) AS applied_amount
                                FROM payroll_deduction_remainders AS r
                                LEFT JOIN
                                    payroll_deduction_remainder_events AS e
                                  ON e.company_id = r.company_id
                                 AND e.remainder_id = r.id
                                WHERE r.company_id = :company
                                  AND r.instruction_id = :instruction
                                GROUP BY
                                    r.id,
                                    r.company_id,
                                    r.instruction_id,
                                    r.original_amount,
                                    r.currency_code
                            """),
                            {
                                "company": company,
                                "instruction": instruction_id,
                            },
                        )
                    ).mappings().one()

                    remittance_row = (
                        await db.execute(
                            text("""
                                SELECT
                                    p.company_id,
                                    p.instruction_id,
                                    p.beneficiary_reference,
                                    p.currency_code,
                                    p.available_amount,
                                    COUNT(e.id) AS event_count,
                                    COALESCE(
                                        SUM(e.amount),
                                        0
                                    ) AS allocated_amount
                                FROM payroll_deduction_remittance_plans AS p
                                LEFT JOIN
                                    payroll_deduction_remittance_events AS e
                                  ON e.company_id = p.company_id
                                 AND e.plan_id = p.id
                                WHERE p.company_id = :company
                                  AND p.instruction_id = :instruction
                                GROUP BY
                                    p.id,
                                    p.company_id,
                                    p.instruction_id,
                                    p.beneficiary_reference,
                                    p.currency_code,
                                    p.available_amount
                            """),
                            {
                                "company": company,
                                "instruction": instruction_id,
                            },
                        )
                    ).mappings().one()

                    assert remainder_row["event_count"] == 2
                    assert remainder_row["applied_amount"] == Decimal("40.00")

                    assert remittance_row["event_count"] == 2
                    assert remittance_row["allocated_amount"] == Decimal("30.00")

                    print("INDEPENDENT SQL AGGREGATION = PASS")

                    # ========================================
                    # 6. BUILD THREE INDEPENDENT SNAPSHOTS
                    # ========================================

                    instruction = InstructionEvidence(
                        company_id=source_row["company_id"],
                        instruction_id=source_row["id"],
                        original_amount=source_row["fixed_amount"],
                        currency_code=source_row["currency_code"],
                    )

                    remainder = RemainderEvidence(
                        company_id=remainder_row["company_id"],
                        instruction_id=remainder_row["instruction_id"],
                        original_amount=remainder_row["original_amount"],
                        currency_code=remainder_row["currency_code"],
                        applied_amount=remainder_row["applied_amount"],
                        remaining_amount=(
                            remainder_row["original_amount"]
                            - remainder_row["applied_amount"]
                        ),
                        event_count=int(remainder_row["event_count"]),
                    )

                    remittance = RemittanceEvidence(
                        company_id=remittance_row["company_id"],
                        instruction_id=remittance_row["instruction_id"],
                        beneficiary_reference=(
                            remittance_row["beneficiary_reference"]
                        ),
                        currency_code=remittance_row["currency_code"],
                        available_amount=remittance_row["available_amount"],
                        allocated_amount=remittance_row["allocated_amount"],
                        remaining_amount=(
                            remittance_row["available_amount"]
                            - remittance_row["allocated_amount"]
                        ),
                        event_count=int(remittance_row["event_count"]),
                    )

                    # ========================================
                    # 7. THREE-SOURCE RECONCILIATION
                    # ========================================

                    result = reconcile_remittance_sources(
                        instruction=instruction,
                        remainder=remainder,
                        remittance=remittance,
                    )

                    assert result.technical_reconciled is True

                    assert (
                        result.instruction_amount
                        == Decimal("100.00")
                    )

                    assert (
                        result.remainder_applied_amount
                        == Decimal("40.00")
                    )

                    assert (
                        result.remainder_remaining_amount
                        == Decimal("60.00")
                    )

                    assert (
                        result.remittance_ceiling
                        == Decimal("80.00")
                    )

                    assert (
                        result.remittance_allocated_amount
                        == Decimal("30.00")
                    )

                    assert (
                        result.remittance_remaining_amount
                        == Decimal("50.00")
                    )

                    print("THREE-SOURCE RECONCILIATION = PASS")
                    print("REMAINDER CONSERVATION = PASS")
                    print("REMITTANCE CONSERVATION = PASS")

                    # ========================================
                    # 8. TAMPERING / ISOLATION PROOF
                    #
                    # These are contradictory snapshots,
                    # not mutations of actual PostgreSQL data.
                    # ========================================

                    def reject(label, expected, **replacement):
                        tampered = replace(
                            replacement.pop("evidence"),
                            **replacement,
                        )

                        return label, expected, tampered

                    tamper_cases = (
                        reject(
                            "SOURCE AMOUNT TAMPERING",
                            "Remainder opening differs",
                            evidence=instruction,
                            original_amount=Decimal("99.00"),
                        ),
                        reject(
                            "REMAINDER COMPANY TAMPERING",
                            "Company isolation mismatch",
                            evidence=remainder,
                            company_id=company + 1,
                        ),
                        reject(
                            "REMITTANCE COMPANY TAMPERING",
                            "Company isolation mismatch",
                            evidence=remittance,
                            company_id=company + 1,
                        ),
                        reject(
                            "CURRENCY TAMPERING",
                            "Currency mismatch",
                            evidence=remittance,
                            currency_code="EUR",
                        ),
                        reject(
                            "INSTRUCTION ID TAMPERING",
                            "Instruction identity mismatch",
                            evidence=remittance,
                            instruction_id=instruction_id + 1,
                        ),
                        reject(
                            "REMITTANCE BALANCE TAMPERING",
                            "Remittance conservation mismatch",
                            evidence=remittance,
                            remaining_amount=Decimal("49.00"),
                        ),
                        reject(
                            "REMITTANCE CEILING TAMPERING",
                            "Remittance ceiling exceeds instruction",
                            evidence=remittance,
                            available_amount=Decimal("110.00"),
                            remaining_amount=Decimal("80.00"),
                        ),
                    )

                    for label, expected_message, tampered in tamper_cases:

                        arguments = {
                            "instruction": instruction,
                            "remainder": remainder,
                            "remittance": remittance,
                        }

                        if isinstance(tampered, InstructionEvidence):
                            arguments["instruction"] = tampered

                        elif isinstance(tampered, RemainderEvidence):
                            arguments["remainder"] = tampered

                        else:
                            arguments["remittance"] = tampered

                        with pytest.raises(
                            RemittanceReconciliationError,
                            match=expected_message,
                        ):
                            reconcile_remittance_sources(
                                **arguments
                            )

                        print(label, "= BLOCKED")

                    # ========================================
                    # 9. TECHNICAL VS FINANCIAL EVIDENCE
                    # ========================================

                    assert result.withheld_funds_verified is False
                    assert result.bank_payment_verified is False
                    assert result.payment_execution_allowed is False
                    assert result.payroll_posting_allowed is False

                    assert (
                        result.legal_authorization
                        == "NOT_AUTHORIZED"
                    )

                    print("WITHHELD FUNDS VERIFIED = FALSE")
                    print("BANK PAYMENT VERIFIED = FALSE")
                    print("PAYMENT EXECUTION = DISABLED")
                    print("PAYROLL POSTING = DISABLED")
                    print("LEGAL AUTHORIZATION = NOT_AUTHORIZED")

            finally:

                if tx.is_active:
                    await tx.rollback()

        # ================================================
        # 10. FRESH CONNECTION: ZERO-LEAK PROOF
        # ================================================

        async with engine.connect() as verification:

            counts = {}

            counts["instruction"] = (
                await verification.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_deduction_instructions
                        WHERE request_key = :request_key
                    """),
                    {"request_key": request_key},
                )
            ).scalar_one()

            counts["remainder"] = (
                await verification.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_deduction_remainders
                        WHERE company_id = :company
                          AND instruction_id = :instruction
                    """),
                    {
                        "company": company,
                        "instruction": instruction_id,
                    },
                )
            ).scalar_one()

            counts["remainder_events"] = (
                await verification.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_deduction_remainder_events
                        WHERE event_id IN (:e1, :e2)
                    """),
                    {
                        "e1": remainder_event_1,
                        "e2": remainder_event_2,
                    },
                )
            ).scalar_one()

            counts["plan"] = (
                await verification.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_deduction_remittance_plans
                        WHERE company_id = :company
                          AND instruction_id = :instruction
                    """),
                    {
                        "company": company,
                        "instruction": instruction_id,
                    },
                )
            ).scalar_one()

            counts["remittance_events"] = (
                await verification.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_deduction_remittance_events
                        WHERE event_id IN (:e1, :e2)
                    """),
                    {
                        "e1": remittance_event_1,
                        "e2": remittance_event_2,
                    },
                )
            ).scalar_one()

            assert all(
                count == 0
                for count in counts.values()
            ), counts

            await verification.rollback()

        print("ROLLBACK ZERO-LEAK = PASS")
        print("PERMANENT TEST BUSINESS ROWS = 0")
        print("REAL POSTGRESQL RECONCILIATION E2E = PASS")

    finally:
        await engine.dispose()
