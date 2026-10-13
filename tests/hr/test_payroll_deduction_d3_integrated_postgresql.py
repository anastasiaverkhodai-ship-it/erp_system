"""Real PostgreSQL E2E for D1 -> D2 -> D3.

MUST run through scripts/run_postgresql_regression_copy.py.

All business fixtures are transaction-scoped and rolled back.
This test does not authorize withholding or journal posting.
"""

import os

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_deduction import (
    PayrollDeductionInstruction,
)
from app.models.payroll_deduction_rule_approval import (
    PayrollDeductionRuleApproval,
)

from app.services.payroll_deduction_accounting_orchestrator import (
    AccountingOrchestrationRequest,
    evaluate_trusted_accounting_orchestration,
)

from app.services.payroll_deduction_approval_preflight_service import (
    DeductionApprovalPreflightError,
)

from app.services.payroll_deduction_result_service import (
    PayrollDeductionResultConflictError,
    require_authorized_deduction_result,
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
async def test_d3_integrated_postgresql_e2e(employee_engine):
    # Never allow fixture writes to the main database.
    assert os.environ.get("RUN_POSTGRES_E2E") == "1", (
        "STOP: isolated PostgreSQL runner required"
    )

    database_url = os.environ.get("DATABASE_URL")

    assert database_url, "STOP: DATABASE_URL missing"

    url = make_url(database_url)

    assert url.get_backend_name() == "postgresql"

    assert (url.database or "").startswith("erp_r3_copy_"), (
        "STOP: non-isolated database"
    )

    instruction_id = None
    approval_id = None

    async with employee_engine.connect() as connection:
        transaction = await connection.begin()

        try:
            actual_database = await connection.scalar(
                text("SELECT current_database()")
            )

            assert actual_database == url.database
            assert actual_database.startswith("erp_r3_copy_")

            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                autoflush=False,
            ) as db:

                company, actor, employee, contract = (
                    await _seed_identity(db)
                )

                foreign_company, foreign_actor, _, _ = (
                    await _seed_identity(db)
                )

                assert company.id != foreign_company.id
                assert actor.id != foreign_actor.id

                instruction = await add_instruction(
                    db,
                    company,
                    actor,
                    contract,
                )

                instruction_id = instruction.id

                # Persist evidence using the actual ERP service.
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

                version = "d3-c2-" + uuid4().hex[:16]
                now = datetime.now(timezone.utc)

                approval = PayrollDeductionRuleApproval(
                    company_id=company.id,
                    legal_category="enforcement_other",
                    rule_version=version,
                    legal_reference=(
                        "Isolated PostgreSQL D3-C2 test"
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

                # Independent approver for the test rule.
                approval.status = "approved"
                approval.approved_by = foreign_actor.id
                approval.approved_at = now

                await db.flush()

                request = AccountingOrchestrationRequest(
                    instruction_id=instruction.id,
                    legal_category="enforcement_other",
                    rule_version=version,
                    priority=1,
                    requested_amount=D("300.00"),
                )

                result = await (
                    evaluate_trusted_accounting_orchestration(
                        db,
                        company_id=company.id,
                        effective_on=DAY,
                        statutory_net=D("1000.00"),
                        global_aggregate_cap=D("500.00"),
                        requests=(request,),
                    )
                )

                assert result.company_id == company.id
                assert len(result.accounting_preflights) == 1

                item = result.accounting_preflights[0]

                assert item.instruction_id == instruction.id
                assert item.requested_amount == D("300.00")

                # Candidate limit from current classification.
                # This is NOT independently verified legal advice.
                assert item.candidate_permitted_amount == D("200.00")
                assert item.candidate_unapplied_amount == D("100.00")

                assert result.batch_preview.total_requested == D("300.00")
                assert result.batch_preview.total_permitted == D("200.00")
                assert result.batch_preview.total_unapplied == D("100.00")

                print("D1 REAL POSTGRESQL APPROVAL = PASS")
                print("D2 REAL POSTGRESQL LIMIT = PASS")
                print("D3 ACCOUNTING PREFLIGHT = PASS")

                assert result.financial_execution_authorized is False
                assert result.journal_posting_allowed is False

                assert item.financial_execution_authorized is False
                assert item.journal_posting_allowed is False

                with pytest.raises(
                    PayrollDeductionResultConflictError
                ):
                    require_authorized_deduction_result(
                        SimpleNamespace(
                            deduction_amount=(
                                item.candidate_permitted_amount
                            )
                        )
                    )

                print("FINANCIAL EXECUTION GUARD = PASS")
                print("JOURNAL POSTING = BLOCKED")

                # Cross-company access must fail.
                with pytest.raises(
                    DeductionApprovalPreflightError
                ):
                    await evaluate_trusted_accounting_orchestration(
                        db,
                        company_id=foreign_company.id,
                        effective_on=DAY,
                        statutory_net=D("1000.00"),
                        global_aggregate_cap=D("500.00"),
                        requests=(request,),
                    )

                print("COMPANY ISOLATION = PASS")

                # A missing rule version cannot reuse another approval.
                wrong_request = AccountingOrchestrationRequest(
                    instruction_id=instruction.id,
                    legal_category="enforcement_other",
                    rule_version="missing-d3-c2-rule",
                    priority=1,
                    requested_amount=D("300.00"),
                )

                with pytest.raises(
                    DeductionApprovalPreflightError
                ):
                    await evaluate_trusted_accounting_orchestration(
                        db,
                        company_id=company.id,
                        effective_on=DAY,
                        statutory_net=D("1000.00"),
                        global_aggregate_cap=D("500.00"),
                        requests=(wrong_request,),
                    )

                print("RULE VERSION ISOLATION = PASS")

        finally:
            if transaction.is_active:
                await transaction.rollback()

    assert instruction_id is not None
    assert approval_id is not None

    # New connection proves absence of persisted test records.
    async with employee_engine.connect() as connection:
        actual_database = await connection.scalar(
            text("SELECT current_database()")
        )

        assert actual_database == url.database

        instruction_remaining = await connection.scalar(
            select(PayrollDeductionInstruction.id).where(
                PayrollDeductionInstruction.id == instruction_id
            )
        )

        approval_remaining = await connection.scalar(
            select(PayrollDeductionRuleApproval.id).where(
                PayrollDeductionRuleApproval.id == approval_id
            )
        )

        assert instruction_remaining is None
        assert approval_remaining is None

    print("ROLLBACK ZERO LEAK = PASS")
    print("D1 -> D2 -> D3 REAL POSTGRESQL = PASS")
    print("FINANCIAL EXECUTION = NOT AUTHORIZED")
