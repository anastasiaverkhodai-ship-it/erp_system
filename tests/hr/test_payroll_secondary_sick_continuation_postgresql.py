"""Real PostgreSQL continuation of internal-secondary incapacity."""
from datetime import date, datetime, UTC

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_secondary_sick_postgresql import prepare

from app.models.leave_request import LeaveRequest
from app.services.payroll_sick_history_service import (
    calculate_sick_from_history,
    PayrollSickLeaveConflictError,
)

pytestmark = pytest.mark.asyncio


async def test_internal_secondary_continuation_reuses_joint_basis(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        first = await calculate_sick_from_history(db, **args)

        assert (
            first.insurance_assessment["combined_basis_scope"]
            == "same_company_imported_internal_secondary"
        )

        continued_leave = LeaveRequest(
            company_id=company.id,
            employment_contract_id=secondary.id,
            leave_type="sick",
            start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 9),
            status="approved",
            requested_by=args["calculated_by"],
            approved_by=args["calculated_by"],
            approved_at=datetime.now(UTC),
        )
        db.add(continued_leave)
        await db.flush()

        continued = await calculate_sick_from_history(
            db,
            **{
                **args,
                "leave_request_id": continued_leave.id,
            },
        )

        assert continued.id != first.id
        assert continued.employer_days == 0
        assert continued.insurer_days == 5
        assert continued.sick_pay_amount > 0
        assert continued.eligible_earnings == first.eligible_earnings
        assert continued.eligible_days == first.eligible_days
        assert (
            continued.insurance_assessment["initial_calculation_id"]
            == first.id
        )
        assert (
            continued.insurance_assessment["combined_insurance_months"]
            == first.insurance_assessment["combined_insurance_months"]
        )

        repeated = await calculate_sick_from_history(
            db,
            **{
                **args,
                "leave_request_id": continued_leave.id,
            },
        )
        assert repeated.id == continued.id


async def test_internal_secondary_continuation_gap_refused(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        await calculate_sick_from_history(db, **args)

        gap_leave = LeaveRequest(
            company_id=company.id,
            employment_contract_id=secondary.id,
            leave_type="sick",
            start_date=date(2026, 10, 7),
            end_date=date(2026, 10, 9),
            status="approved",
            requested_by=args["calculated_by"],
            approved_by=args["calculated_by"],
            approved_at=datetime.now(UTC),
        )
        db.add(gap_leave)
        await db.flush()

        with pytest.raises(
            PayrollSickLeaveConflictError,
            match="gap",
        ):
            await calculate_sick_from_history(
                db,
                **{
                    **args,
                    "leave_request_id": gap_leave.id,
                },
            )
