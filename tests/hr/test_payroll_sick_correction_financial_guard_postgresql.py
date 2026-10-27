"""Isolated PostgreSQL sick correction financial preflight."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_secondary_sick_postgresql import prepare

from app.services.payroll_sick_history_service import (
    calculate_sick_from_history,
)
from app.services.payroll_sick_correction_financial_guard import (
    require_sick_replacement_financially_clear,
)
from app.services.payroll_sick_leave_service import (
    PayrollSickLeaveNotFoundError,
)

pytestmark = pytest.mark.asyncio


async def test_unlinked_original_can_enter_financial_preflight(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = await prepare(db)
        original = await calculate_sick_from_history(db, **args)

        result = await require_sick_replacement_financially_clear(
            db,
            company_id=company.id,
            original_calculation_id=original.id,
        )

        assert result.id == original.id
        assert result.sick_pay_amount == original.sick_pay_amount


async def test_missing_original_refused(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = await prepare(db)

        with pytest.raises(PayrollSickLeaveNotFoundError):
            await require_sick_replacement_financially_clear(
                db,
                company_id=company.id,
                original_calculation_id=999999999,
            )
