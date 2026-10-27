
"""Verify no duplicate sick payout across employee contracts."""

from datetime import datetime, UTC

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_secondary_sick_postgresql import prepare

from app.models.leave_request import LeaveRequest
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.services.payroll_sick_history_service import (
    calculate_sick_from_history,
    PayrollSickLeaveConflictError,
)

pytestmark = pytest.mark.asyncio


async def test_overlapping_primary_and_secondary_calculation_refused(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        first = await calculate_sick_from_history(
            db, **args
        )

        assert first.employment_contract_id == secondary.id
        assert first.sick_pay_amount > 0

        original = await db.get(
            LeaveRequest, args["leave_request_id"]
        )
        assert original is not None

        primary_leave = LeaveRequest(
            company_id=company.id,
            employment_contract_id=primary.id,
            leave_type="sick",
            start_date=original.start_date,
            end_date=original.end_date,
            status="approved",
            requested_by=args["calculated_by"],
            approved_by=args["calculated_by"],
            approved_at=datetime.now(UTC),
        )
        db.add(primary_leave)
        await db.flush()

        with pytest.raises(
            PayrollSickLeaveConflictError,
            match="Later or overlapping sick calculation",
        ):
            await calculate_sick_from_history(
                db,
                company_id=company.id,
                leave_request_id=primary_leave.id,
                insurance_evidence_id=args["insurance_evidence_id"],
                calculated_by=args["calculated_by"],
            )

        calculations = (
            await db.scalars(
                select(PayrollSickLeaveCalculation).where(
                    PayrollSickLeaveCalculation.company_id == company.id,
                    PayrollSickLeaveCalculation.employment_contract_id.in_(
                        [primary.id, secondary.id]
                    ),
                )
            )
        ).all()

        assert len(calculations) == 1
        assert calculations[0].id == first.id
        assert calculations[0].sick_pay_amount == first.sick_pay_amount

        repeated = await calculate_sick_from_history(
            db, **args
        )
        assert repeated.id == first.id


async def test_overlap_refusal_does_not_create_primary_sick_payment(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        first = await calculate_sick_from_history(
            db, **args
        )

        original = await db.get(
            LeaveRequest, args["leave_request_id"]
        )

        primary_leave = LeaveRequest(
            company_id=company.id,
            employment_contract_id=primary.id,
            leave_type="sick",
            start_date=original.start_date,
            end_date=original.end_date,
            status="approved",
            requested_by=args["calculated_by"],
            approved_by=args["calculated_by"],
            approved_at=datetime.now(UTC),
        )
        db.add(primary_leave)
        await db.flush()

        with pytest.raises(PayrollSickLeaveConflictError):
            await calculate_sick_from_history(
                db,
                company_id=company.id,
                leave_request_id=primary_leave.id,
                insurance_evidence_id=args["insurance_evidence_id"],
                calculated_by=args["calculated_by"],
            )

        primary_count = await db.scalar(
            select(func.count())
            .select_from(PayrollSickLeaveCalculation)
            .where(
                PayrollSickLeaveCalculation.company_id == company.id,
                PayrollSickLeaveCalculation.employment_contract_id
                == primary.id,
            )
        )
        assert primary_count == 0

        secondary_count = await db.scalar(
            select(func.count())
            .select_from(PayrollSickLeaveCalculation)
            .where(
                PayrollSickLeaveCalculation.company_id == company.id,
                PayrollSickLeaveCalculation.employment_contract_id
                == secondary.id,
            )
        )
        assert secondary_count == 1
