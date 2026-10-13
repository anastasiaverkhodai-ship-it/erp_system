"""13.6-D4-C2: real PostgreSQL D1-D4 integrated acceptance.

Runs ONLY with the isolated regression-copy runner.

Technical remittance plans do not prove actual withholding.
No payroll deduction execution, posting or bank payment occurs.
All fixture rows are rolled back.
"""

import os

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_entry import JournalEntry

from app.models.payroll_deduction import (
    PayrollDeductionInstruction,
)

from app.models.payroll_deduction_remainder import (
    PayrollDeductionRemainder,
)

from app.models.payroll_deduction_remittance import (
    PayrollDeductionRemittancePlan,
)

from app.models.payroll_deduction_rule_approval import (
    PayrollDeductionRuleApproval,
)

from app.services.payroll_deduction_remittance_db_bridge import (
    RemittanceDatabaseBridgeError,
    evaluate_database_backed_remittance_readiness,
)

from app.services.payroll_deduction_remainder_persistence_service import (
    open_persisted_remainder,
)

from app.services.payroll_deduction_remittance_persistence_service import (
    open_persisted_remittance_plan,
)

from test_employee_foundation import employee_engine

from test_payroll_deduction_postgresql import (
    _seed_identity,
)

from test_payroll_deduction_acceptance_postgresql import (
    add_instruction,
    pending,
    verify,
)


D = Decimal
DAY = date(2026, 10, 13)


@pytest.mark.asyncio
async def test_d4_integrated_database_bridge_postgresql(
    employee_engine,
):
    # Do not create test data before isolation checks.
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    raw_url = os.environ.get("DATABASE_URL")
    assert raw_url

    url = make_url(raw_url)

    assert url.get_backend_name() == "postgresql"
    assert (url.database or "").startswith("erp_r3_copy_")

    instruction_id = None
    approval_id = None
    remainder_id = None
    remittance_plan_id = None

    async with employee_engine.connect() as connection:
        transaction = await connection.begin()

        try:
            db_name = await connection.scalar(
                text("SELECT current_database()")
            )

            assert db_name == url.database
            assert db_name.startswith("erp_r3_copy_")

            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                autoflush=False,
            ) as db:

                # Create isolated companies, actors and contracts.
                company, actor, _, contract = (
                    await _seed_identity(db)
                )

                foreign_company, foreign_actor, _, _ = (
                    await _seed_identity(db)
                )

                assert company.id != foreign_company.id
                assert actor.id != foreign_actor.id

                # Create an actual ORM instruction.
                instruction = await add_instruction(
                    db,
                    company,
                    actor,
                    contract,
                )

                instruction_id = instruction.id

                assert instruction.company_id == company.id
                assert instruction.method == "fixed"
                assert instruction.fixed_amount == D("1000.00")
                assert instruction.currency_code == "UAH"

                # D1: create and verify evidence.
                evidence = await pending(
                    db,
                    company,
                    actor,
                    instruction,
                    category="enforcement_other",
                )

                await db.flush()

                verified = await verify(
                    db,
                    company,
                    actor,
                    evidence,
                )

                assert verified is not None

                await db.flush()

                now = datetime.now(timezone.utc)
                version = "d4-c2-" + uuid4().hex[:16]

                approval = PayrollDeductionRuleApproval(
                    company_id=company.id,
                    legal_category="enforcement_other",
                    rule_version=version,
                    legal_reference=(
                        "Isolated D4 integration fixture"
                    ),
                    valid_from=date(2026, 1, 1),
                    valid_to=date(2026, 12, 31),
                    status="pending",
                    created_by=actor.id,
                    created_at=now - timedelta(minutes=1),
                    approved_by=None,
                    approved_at=None,
                    revoked_by=None,
                    revoked_at=None,
                )

                db.add(approval)
                await db.flush()

                approval_id = approval.id

                approval.status = "approved"
                approval.approved_by = foreign_actor.id
                approval.approved_at = now

                await db.flush()

                # Capture journal count before calling D4.
                journals_before = await db.scalar(
                    select(func.count())
                    .select_from(JournalEntry)
                    .where(
                        JournalEntry.company_id == company.id
                    )
                )

                # Create technical remainder opening.
                # 1000 is the nominal instruction amount.
                # It is NOT an actual withheld payroll amount.
                remainder = await open_persisted_remainder(
                    db=db,
                    company_id=company.id,
                    instruction_id=instruction.id,
                    original_amount=D("1000.00"),
                    currency_code="UAH",
                    actor_id=actor.id,
                )

                remainder_id = remainder.remainder_id

                assert remainder.original_amount == D("1000.00")
                assert remainder.applied_amount == D("0.00")
                assert remainder.remaining_amount == D("1000.00")
                assert remainder.event_count == 0

                print("PERSISTED REMAINDER OPENING = PASS")

                # Create a technical remittance plan.
                # Its ceiling is NOT bank-payment authorization.
                plan, initial_balance, replay = (
                    await open_persisted_remittance_plan(
                        db=db,
                        company_id=company.id,
                        instruction_id=instruction.id,
                        beneficiary_reference=(
                            "D4-C2-ISOLATED-TEST-BENEFICIARY"
                        ),
                        currency_code="UAH",
                        available_amount=D("80.00"),
                        created_by=actor.id,
                    )
                )

                remittance_plan_id = plan.id

                assert replay is False
                assert initial_balance.available_amount == D("80.00")
                assert initial_balance.allocated_amount == D("0.00")
                assert initial_balance.remaining_amount == D("80.00")
                assert initial_balance.event_count == 0

                assert initial_balance.payment_execution_allowed is False

                print("PERSISTED REMITTANCE OPENING = PASS")

                # D4 bridge must independently read the
                # instruction, D1/D2/D3 and both ledgers.
                bridge_args = dict(
                    company_id=company.id,
                    instruction_id=instruction.id,
                    legal_category="enforcement_other",
                    rule_version=version,
                    effective_on=DAY,
                    priority=1,
                    requested_amount=D("300.00"),
                    statutory_net=D("1000.00"),
                    global_aggregate_cap=D("500.00"),
                )

                result = await (
                    evaluate_database_backed_remittance_readiness(
                        db,
                        **bridge_args,
                    )
                )

                assert result.company_id == company.id
                assert result.instruction_id == instruction.id

                # Existing candidate classification behaviour.
                # This is not a verified legal withholding rule.
                assert (
                    result.candidate_permitted_amount
                    == D("200.00")
                )
                assert (
                    result.candidate_unapplied_amount
                    == D("100.00")
                )

                assert result.technical_reconciled is True

                assert result.withheld_funds_verified is False
                assert result.bank_payment_verified is False

                assert (
                    result.financial_execution_authorized
                    is False
                )
                assert result.journal_posting_allowed is False
                assert result.payment_execution_allowed is False
                assert result.legal_authorization == "NOT_AUTHORIZED"

                print("D1 PERSISTED VERIFIED EVIDENCE = PASS")
                print("D1 TRUSTED APPROVAL = PASS")
                print("D2 CANDIDATE LIMIT PREVIEW = PASS")
                print("D3 ACCOUNTING PREFLIGHT = PASS")
                print("D4 DATABASE-BACKED BRIDGE = PASS")
                print("THREE-SOURCE RECONCILIATION = PASS")
                print("WITHHELD FUNDS VERIFIED = FALSE")
                print("BANK PAYMENT VERIFIED = FALSE")
                print("PAYMENT EXECUTION = BLOCKED")

                # Another company may not read the instruction.
                foreign_args = dict(bridge_args)
                foreign_args["company_id"] = foreign_company.id

                with pytest.raises(
                    RemittanceDatabaseBridgeError,
                    match="Instruction not found",
                ):
                    await (
                        evaluate_database_backed_remittance_readiness(
                            db,
                            **foreign_args,
                        )
                    )

                print("CROSS-COMPANY BRIDGE ISOLATION = PASS")

                # A valid plan must not validate an unknown rule.
                invalid_rule_args = dict(bridge_args)
                invalid_rule_args["rule_version"] = (
                    "missing-d4-c2-version"
                )

                with pytest.raises(ValueError):
                    await (
                        evaluate_database_backed_remittance_readiness(
                            db,
                            **invalid_rule_args,
                        )
                    )

                print("APPROVAL VERSION ISOLATION = PASS")

                # A bridge evaluation cannot create a journal.
                journals_after = await db.scalar(
                    select(func.count())
                    .select_from(JournalEntry)
                    .where(
                        JournalEntry.company_id == company.id
                    )
                )

                assert journals_after == journals_before

                # Read-only bridge calls must not add
                # remittance or remainder events.
                assert await db.scalar(
                    text(
                        "SELECT COUNT(*) "
                        "FROM payroll_deduction_remittance_events "
                        "WHERE company_id = :company_id "
                        "AND plan_id = :plan_id"
                    ),
                    {
                        "company_id": company.id,
                        "plan_id": plan.id,
                    },
                ) == 0

                assert await db.scalar(
                    text(
                        "SELECT COUNT(*) "
                        "FROM payroll_deduction_remainder_events "
                        "WHERE company_id = :company_id "
                        "AND remainder_id = :remainder_id"
                    ),
                    {
                        "company_id": company.id,
                        "remainder_id": remainder_id,
                    },
                ) == 0

                print("ACCOUNTING JOURNAL DELTA = ZERO")
                print("REMITTANCE EVENT DELTA = ZERO")
                print("REMAINDER EVENT DELTA = ZERO")

        finally:
            # Never commit fixture writes.
            if transaction.is_active:
                await transaction.rollback()

    assert instruction_id is not None
    assert approval_id is not None
    assert remainder_id is not None
    assert remittance_plan_id is not None

    # Independent new connection proves rollback.
    async with employee_engine.connect() as connection:
        assert await connection.scalar(
            text("SELECT current_database()")
        ) == url.database

        instruction_after = await connection.scalar(
            select(PayrollDeductionInstruction.id).where(
                PayrollDeductionInstruction.id == instruction_id
            )
        )

        approval_after = await connection.scalar(
            select(PayrollDeductionRuleApproval.id).where(
                PayrollDeductionRuleApproval.id == approval_id
            )
        )

        remainder_after = await connection.scalar(
            select(PayrollDeductionRemainder.id).where(
                PayrollDeductionRemainder.id == remainder_id
            )
        )

        remittance_after = await connection.scalar(
            select(PayrollDeductionRemittancePlan.id).where(
                PayrollDeductionRemittancePlan.id
                == remittance_plan_id
            )
        )

        assert instruction_after is None
        assert approval_after is None
        assert remainder_after is None
        assert remittance_after is None

    print("ROLLBACK ZERO LEAK = PASS")
    print("D1 -> D2 -> D3 -> D4 POSTGRESQL E2E = PASS")
    print("FINANCIAL EXECUTION = NOT AUTHORIZED")
    print("ACCOUNTING POSTING = NOT AUTHORIZED")
    print("BANK PAYMENT = NOT AUTHORIZED")
