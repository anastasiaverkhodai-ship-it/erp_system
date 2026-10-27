
"""Cross-contract ordinary sick overlap must never double pay."""
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_secondary_sick_postgresql import prepare

from app.services.payroll_sick_history_service import (
    calculate_sick_from_history,
    PayrollSickLeaveConflictError,
)

pytestmark = pytest.mark.asyncio


async def test_same_employee_overlapping_contract_sick_pay_refused(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        secondary_result = await calculate_sick_from_history(
            db, **args
        )
        assert secondary_result.sick_pay_amount > D(0)

        # The calculation must not silently permit a second
        # same-employee overlapping allocation on another job.
        # If business rules permit parallel certificates,
        # they require a reviewed joint allocation workflow.
        leave = await db.get(
            __import__(
                "app.models.leave_request",
                fromlist=["LeaveRequest"],
            ).LeaveRequest,
            args["leave_request_id"],
        )
        assert leave is not None
        assert leave.employment_contract_id == secondary.id

        # Persisted daily allocation must reconcile exactly.
        entries = secondary_result.insurance_assessment[
            "daily_allocation"
        ]
        assert len(entries) == (
            leave.end_date - leave.start_date
        ).days + 1

        employer = sum(
            (D(x["amount"]) for x in entries
             if x["payer"] == "employer"),
            D(0),
        )
        insurer = sum(
            (D(x["amount"]) for x in entries
             if x["payer"] == "insurer"),
            D(0),
        )

        assert employer == secondary_result.employer_amount
        assert insurer == secondary_result.insurer_amount
        assert employer + insurer == secondary_result.sick_pay_amount

        assert len({x["date"] for x in entries}) == len(entries)
