import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_dated_employee_statutory_postgresql import setup

from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
)
from app.services.payroll_regulatory_active_financial_review import (
    review_persisted_active_financials,
)

pytestmark = pytest.mark.asyncio


async def test_real_active_financial_evidence(employee_engine):
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

        result = await review_persisted_active_financials(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        print("ACTIVE FINANCIAL STATUS =", result["status"])
        print("ACTIVE FINANCIAL BASE =", result.get("active_base_total"))
        print(
            "ACTIVE FINANCIAL SOURCES =",
            result.get("active_allocated_total"),
        )

        assert result["status"] == "ACTIVE_FINANCIAL_RECONCILED", result
        assert result["active_calculation_count"] == 1
        assert result["historical_calculation_count"] == 0
        assert result["active_base_total"] == result["active_allocated_total"]
        assert result["correction_delta_verified"] is False
        assert result["official_generation"] == "BLOCKED"

        print("REAL ACTIVE FINANCIAL E2E = PASS")

        await db.rollback()
