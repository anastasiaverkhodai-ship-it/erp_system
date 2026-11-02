import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_dated_employee_statutory_postgresql import setup

from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
)
from app.services.payroll_regulatory_period_coverage import (
    review_persisted_period_coverage,
)

pytestmark = pytest.mark.asyncio


async def test_positive_full_period_source_coverage(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, employee, contract, calculation = (
            await setup(db)
        )

        await calculate_payroll_statutory_result(
            db,
            company_id=company.id,
            payroll_calculation_id=calculation.id,
            actor_user_id=user.id,
        )

        result = await review_persisted_period_coverage(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        print("PERIOD COVERAGE STATUS =", result["status"])
        print("PERIOD CALCULATIONS =", result["calculation_count"])
        print("PERIOD STATUTORY RESULTS =", result["statutory_result_count"])
        print("PERIOD ALLOCATION =", result["allocation_status"])

        assert result["status"] == (
            "PERIOD_FINANCIAL_COVERAGE_VERIFIED"
        ), result

        assert result["calculation_count"] == 1
        assert result["statutory_result_count"] == 1
        assert result["financial_allocation_verified"] is True
        assert result["allocation_status"] == (
            "FINANCIAL_LINES_RECONCILED"
        )
        assert result["official_generation"] == "BLOCKED"

        print("POSITIVE FULL PERIOD COVERAGE = PASS")
        await db.rollback()
