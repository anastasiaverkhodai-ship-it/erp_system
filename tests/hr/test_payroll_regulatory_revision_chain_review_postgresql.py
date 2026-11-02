import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_dated_employee_statutory_postgresql import setup

from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
)
from app.services.payroll_regulatory_revision_chain_review import (
    review_persisted_revision_chain,
)

pytestmark = pytest.mark.asyncio


async def test_real_single_revision_chain(employee_engine):
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

        result = await review_persisted_revision_chain(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        print("REVISION CHAIN STATUS =", result["status"])
        print("ACTIVE CALCULATIONS =", result.get(
            "active_calculation_ids"
        ))
        print("HISTORICAL CALCULATIONS =", result.get(
            "historical_calculation_ids"
        ))

        assert result["status"] == "REVISION_CHAIN_VERIFIED", result
        assert result["active_calculation_ids"] == [calculation.id]
        assert result["historical_calculation_ids"] == []
        assert result["data_origin"] == "PERSISTED_POSTGRESQL"
        assert result["official_generation"] == "BLOCKED"

        print("REAL POSTGRESQL REVISION CHAIN = PASS")

        await db.rollback()
