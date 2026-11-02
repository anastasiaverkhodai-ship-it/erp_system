import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_dated_employee_statutory_postgresql import setup

from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
)
from app.services.payroll_regulatory_persisted_correction_delta import (
    review_persisted_correction_delta,
)

pytestmark = pytest.mark.asyncio


async def test_real_payroll_without_replacement_is_blocked(
    employee_engine,
):
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

        result = await review_persisted_correction_delta(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        print(
            "PERSISTED CORRECTION DELTA STATUS =",
            result["status"],
        )
        print(
            "PERSISTED CORRECTION DELTA REASON =",
            result["reason"],
        )

        assert result["status"] == "NOT_VERIFIED"
        assert result["reason"] == (
            "NO_PERSISTED_REPLACEMENT_CORRECTION"
        )
        assert result["persisted_correction_delta_verified"] is False
        assert result["official_generation"] == "BLOCKED"

        print("REAL POSTGRESQL NO-CORRECTION GUARD = PASS")

        await db.rollback()
