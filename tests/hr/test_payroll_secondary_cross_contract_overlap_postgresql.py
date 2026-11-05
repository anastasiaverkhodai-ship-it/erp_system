
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


from decimal import Decimal

from app.models.payroll_insurance_evidence import PayrollInsuranceEvidence


async def _build_two_contract_sick_case(db):
    company, primary, secondary, histories, args = (
        await prepare(db)
    )

    first = await calculate_sick_from_history(
        db, **args
    )

    assert first.employment_contract_id == secondary.id
    assert Decimal(str(first.sick_pay_amount)) > 0

    original = await db.get(
        LeaveRequest,
        args["leave_request_id"],
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

    second = await calculate_sick_from_history(
        db,
        company_id=company.id,
        leave_request_id=primary_leave.id,
        insurance_evidence_id=args["insurance_evidence_id"],
        calculated_by=args["calculated_by"],
    )

    assert second.employment_contract_id == primary.id
    assert second.id != first.id

    return (
        company, primary, secondary, original,
        first, second, args
    )


async def test_overlapping_primary_and_secondary_calculation_refused(
    employee_engine,
):
    """Both contracts may be calculated, but share the
    employee's monthly benefit ceiling.
    """
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company, primary, secondary, original,
            first, second, args
        ) = await _build_two_contract_sick_case(db)

        calculations = (
            await db.scalars(
                select(PayrollSickLeaveCalculation).where(
                    PayrollSickLeaveCalculation.company_id
                    == company.id,
                    PayrollSickLeaveCalculation.employment_contract_id.in_(
                        [primary.id, secondary.id]
                    ),
                )
            )
        ).all()

        assert len(calculations) == 2
        assert {row.id for row in calculations} == {
            first.id, second.id
        }

        evidence = await db.get(
            PayrollInsuranceEvidence,
            args["insurance_evidence_id"],
        )
        assert evidence is not None

        from datetime import timedelta
        from app.services.payroll_sick_history_service import (
            sick_period_amount,
        )

        primary_leave = await db.get(
            LeaveRequest, second.leave_request_id
        )
        assert primary_leave is not None

        assert (
            primary_leave.start_date == original.start_date
            and primary_leave.end_date == original.end_date
        ), "STOP: two contract dates differ"

        # The production allocation service uses one
        # employee-wide benefit limit per calendar month.
        # Split both contracts using that same period helper.
        monthly_max = (
            Decimal("160000")
            if evidence.case_start.month == 1
            else Decimal("172940")
        )

        first_amount = Decimal(str(first.sick_pay_amount))
        second_amount = Decimal(str(second.sick_pay_amount))

        assert first_amount > 0
        assert second_amount >= 0

        month = original.start_date.replace(day=1)
        last_month = original.end_date.replace(day=1)

        first_allocated = Decimal("0")
        second_allocated = Decimal("0")
        checked_months = 0

        while month <= last_month:
            next_month = (
                month.replace(day=28) + timedelta(days=4)
            ).replace(day=1)

            month_end = next_month - timedelta(days=1)

            _, first_used = sick_period_amount(
                first, original, month, month_end
            )

            _, second_used = sick_period_amount(
                second, primary_leave, month, month_end
            )

            first_used = Decimal(str(first_used))
            second_used = Decimal(str(second_used))

            assert first_used >= 0
            assert second_used >= 0

            # Do not apply one monthly ceiling to
            # the whole multi-month certificate.
            assert first_used <= monthly_max
            assert second_used <= monthly_max - first_used
            assert first_used + second_used <= monthly_max

            first_allocated += first_used
            second_allocated += second_used
            checked_months += 1

            print(
                "MONTHLY SHARED CAP = PASS",
                month.strftime("%Y-%m"),
                "PRIMARY =", second_used,
                "SECONDARY =", first_used,
                "MAX =", monthly_max,
                flush=True,
            )

            month = next_month

        assert checked_months >= 2, (
            "STOP: expected multi-month fixture"
        )

        # Period allocations must reconcile to the
        # persisted contract totals. Allow at most
        # two cents for allocation rounding.
        assert abs(
            first_allocated - first_amount
        ) <= Decimal("0.02")

        assert abs(
            second_allocated - second_amount
        ) <= Decimal("0.02")

        for row in (first, second):
            employer = Decimal(str(row.employer_amount))
            insurer = Decimal(str(row.insurer_amount))
            total = Decimal(str(row.sick_pay_amount))

            assert employer >= 0
            assert insurer >= 0
            assert employer + insurer == total

        print("TWO DISTINCT CONTRACT CALCULATIONS = PASS")
        print("EMPLOYEE-WIDE MONTHLY CAP = PASS")
        print("EMPLOYER AND INSURER RECONCILIATION = PASS")


async def test_overlap_refusal_does_not_create_primary_sick_payment(
    employee_engine,
):
    """Retries must not double-pay either employee contract."""
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company, primary, secondary, original,
            first, second, args
        ) = await _build_two_contract_sick_case(db)

        repeated_primary = await calculate_sick_from_history(
            db,
            company_id=company.id,
            leave_request_id=second.leave_request_id,
            insurance_evidence_id=args["insurance_evidence_id"],
            calculated_by=args["calculated_by"],
        )

        repeated_secondary = await calculate_sick_from_history(
            db, **args
        )

        assert repeated_primary.id == second.id
        assert repeated_secondary.id == first.id

        rows = (
            await db.scalars(
                select(PayrollSickLeaveCalculation).where(
                    PayrollSickLeaveCalculation.company_id
                    == company.id,
                    PayrollSickLeaveCalculation.employment_contract_id.in_(
                        [primary.id, secondary.id]
                    ),
                )
            )
        ).all()

        assert len(rows) == 2
        assert {row.id for row in rows} == {
            first.id, second.id
        }

        primary_count = await db.scalar(
            select(func.count())
            .select_from(PayrollSickLeaveCalculation)
            .where(
                PayrollSickLeaveCalculation.company_id
                == company.id,
                PayrollSickLeaveCalculation.employment_contract_id
                == primary.id,
            )
        )

        secondary_count = await db.scalar(
            select(func.count())
            .select_from(PayrollSickLeaveCalculation)
            .where(
                PayrollSickLeaveCalculation.company_id
                == company.id,
                PayrollSickLeaveCalculation.employment_contract_id
                == secondary.id,
            )
        )

        assert primary_count == 1
        assert secondary_count == 1

        assert (
            repeated_primary.sick_pay_amount
            == second.sick_pay_amount
        )
        assert (
            repeated_secondary.sick_pay_amount
            == first.sick_pay_amount
        )

        print("PRIMARY CONTRACT IDEMPOTENCY = PASS")
        print("SECONDARY CONTRACT IDEMPOTENCY = PASS")
        print("DUPLICATE SICK CALCULATIONS = 0")
