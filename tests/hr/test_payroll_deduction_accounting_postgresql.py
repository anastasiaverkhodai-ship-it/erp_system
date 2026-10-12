import os
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import (
    PayrollPeriod,
    PayrollInput,
    PayrollCalculation,
)
from app.models.payroll_statutory import PayrollStatutoryResult
from app.models.payroll_deduction_result import PayrollDeductionResult
from app.models.journal_entry import JournalEntry

from app.services.payroll_deduction_accounting_service import (
    generate_and_post_payroll_deduction_journal,
)
from app.services.payroll_deduction_result_service import (
    PayrollDeductionResultConflictError,
)

from test_employee_foundation import employee_engine
from test_payroll_deduction_postgresql import _seed_identity


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Real isolated PostgreSQL required",
    ),
]


async def test_unauthorized_deduction_cannot_post_journal(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
        autoflush=False,
    ) as db:
        try:
            company, actor, _, contract = await _seed_identity(db)

            period = PayrollPeriod(
                company_id=company.id,
                year=2026,
                month=9,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 30),
                created_by=actor.id,
            )
            db.add(period)
            await db.flush()

            payroll_input = PayrollInput(
                company_id=company.id,
                payroll_period_id=period.id,
                employment_contract_id=contract.id,
                scheduled_minutes=6000,
                worked_minutes=6000,
                created_by=actor.id,
            )
            db.add(payroll_input)
            await db.flush()

            calculation = PayrollCalculation(
                company_id=company.id,
                payroll_period_id=period.id,
                payroll_input_id=payroll_input.id,
                employment_contract_id=contract.id,
                currency_code="UAH",
                gross_amount=Decimal("10000.00"),
                calculated_by=actor.id,
                calculated_at=datetime.now(timezone.utc),
            )
            db.add(calculation)
            await db.flush()

            statutory = PayrollStatutoryResult(
                company_id=company.id,
                payroll_calculation_id=calculation.id,
                currency_code="UAH",
                gross_amount=Decimal("10000.00"),
                employee_withholding_amount=Decimal("2300.00"),
                employer_contribution_amount=Decimal("2200.00"),
                net_amount=Decimal("7700.00"),
                calculated_by=actor.id,
                calculated_at=datetime.now(timezone.utc),
            )
            db.add(statutory)
            await db.flush()

            deduction = PayrollDeductionResult(
                company_id=company.id,
                payroll_calculation_id=calculation.id,
                payroll_statutory_result_id=statutory.id,
                employment_contract_id=contract.id,
                statutory_net_amount=Decimal("7700.00"),
                deduction_amount=Decimal("654.00"),
                final_payable_amount=Decimal("7046.00"),
                currency_code="UAH",
                calculated_by=actor.id,
                calculated_at=datetime.now(timezone.utc),
            )
            db.add(deduction)
            await db.flush()

            before = await db.scalar(
                select(func.count())
                .select_from(JournalEntry)
                .where(JournalEntry.company_id == company.id)
            )

            with pytest.raises(
                PayrollDeductionResultConflictError,
                match="not authorized",
            ):
                await generate_and_post_payroll_deduction_journal(
                    db,
                    company_id=company.id,
                    payroll_deduction_result_id=deduction.id,
                    created_by=actor.id,
                )

            after = await db.scalar(
                select(func.count())
                .select_from(JournalEntry)
                .where(JournalEntry.company_id == company.id)
            )

            assert after == before
            assert Decimal(deduction.deduction_amount) == Decimal("654.00")

        finally:
            await db.rollback()
