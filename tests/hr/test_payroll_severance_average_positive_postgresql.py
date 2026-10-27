"""Real PostgreSQL positive integration: two completed payroll months.

Runs only on the disposable database copy. Fixture amounts are
illustrative and do not establish statutory income classification.
"""

import os
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_source_locking import sources

from app.models.payroll import (
    PayrollPeriod,
    PayrollInput,
    PayrollCalculation,
    PayrollCalculationLine,
)
from app.models.time_attendance import (
    WorkSchedule,
    WorkScheduleDay,
    EmploymentScheduleAssignment,
    AttendanceRecord,
)
from app.services.payroll_severance_database_average import (
    derive_database_severance_average,
)
from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Isolated PostgreSQL required",
    ),
]


@pytest.mark.parametrize("scenario", ['positive', 'unfinalized_period', 'wrong_currency', 'gross_mismatch', 'attendance_mismatch', 'unverified_minimum_wage'])
async def test_positive_two_month_average(employee_engine, scenario):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        fixtures, contract, _ = await sources(db)

        company_id = fixtures["company"]
        actor_id = fixtures["actor"]

        first = date(2026, 7, 1)
        last = date(2026, 8, 31)

        assert contract.start_date <= first
        assert (
            contract.end_date is None
            or contract.end_date >= last
        )

        # Reuse the fixture's existing schedule, without modifying it.
        assignments = list((
            await db.scalars(
                select(EmploymentScheduleAssignment).where(
                    EmploymentScheduleAssignment.company_id
                    == company_id,
                    EmploymentScheduleAssignment.employment_contract_id
                    == contract.id,
                    EmploymentScheduleAssignment.effective_from
                    <= first,
                    (
                        EmploymentScheduleAssignment.effective_to.is_(None)
                        | (
                            EmploymentScheduleAssignment.effective_to
                            >= last
                        )
                    ),
                )
            )
        ).all())

        assert len(assignments) == 1, (
            "Fixture must have exactly one assignment "
            "covering the entire two-month period"
        )

        assignment = assignments[0]

        schedule = await db.get(
            WorkSchedule, assignment.work_schedule_id
        )

        assert schedule is not None
        assert schedule.company_id == company_id
        assert schedule.is_active is True

        schedule_days = list((
            await db.scalars(
                select(WorkScheduleDay).where(
                    WorkScheduleDay.company_id == company_id,
                    WorkScheduleDay.work_schedule_id == schedule.id,
                )
            )
        ).all())

        by_weekday = {
            day.weekday: day
            for day in schedule_days
        }

        assert set(by_weekday) == {1, 2, 3, 4, 5}

        for weekday in range(1, 6):
            day = by_weekday[weekday]
            assert day.is_working_day is True
            assert day.start_time is not None
            assert day.end_time is not None

        total_days = 0

        for month_number in (7, 8):
            month_start = date(2026, month_number, 1)
            month_end = date(
                2026,
                month_number,
                monthrange(2026, month_number)[1],
            )

            period = PayrollPeriod(
                company_id=company_id,
                year=2026,
                month=month_number,
                start_date=month_start,
                end_date=month_end,
                status=(
                    "draft"
                    if scenario == "unfinalized_period"
                    and month_number == 8
                    else "finalized"
                ),
                created_by=actor_id,
                finalized_by=(
                    None
                    if scenario == "unfinalized_period"
                    and month_number == 8
                    else actor_id
                ),
                finalized_at=(
                    None
                    if scenario == "unfinalized_period"
                    and month_number == 8
                    else datetime.now(timezone.utc)
                ),
            )
            db.add(period)
            await db.flush()

            working_dates = []
            cursor = month_start

            while cursor <= month_end:
                if cursor.isoweekday() <= 5:
                    working_dates.append(cursor)
                cursor += timedelta(days=1)

            for work_date in working_dates:
                start_at = datetime(
                    work_date.year, work_date.month,
                    work_date.day, 9, 0,
                    tzinfo=timezone.utc,
                )
                db.add(
                    AttendanceRecord(
                        company_id=company_id,
                        employment_contract_id=contract.id,
                        work_date=work_date,
                        status="present",
                        actual_start_at=start_at,
                        actual_end_at=start_at
                        + timedelta(hours=8),
                        break_minutes=0,
                        worked_minutes=480,
                        source="system",
                        created_by=actor_id,
                    )
                )

            count = len(working_dates)
            total_days += count

            payroll_input = PayrollInput(
                company_id=company_id,
                payroll_period_id=period.id,
                employment_contract_id=contract.id,
                monthly_norm_minutes=count * 480,
                scheduled_minutes=count * 480,
                worked_minutes=(
                    count * 480 - 480
                    if scenario == "attendance_mismatch"
                    and month_number == 8
                    else count * 480
                ),
                leave_days=0,
                sick_days=0,
                manual_adjustment_amount=D("0.00"),
                source="system",
                created_by=actor_id,
            )
            db.add(payroll_input)
            await db.flush()

            calculation = PayrollCalculation(
                company_id=company_id,
                payroll_period_id=period.id,
                payroll_input_id=payroll_input.id,
                employment_contract_id=contract.id,
                revision=1,
                status="calculated",
                currency_code=(
                    "USD"
                    if scenario == "wrong_currency"
                    and month_number == 8
                    else "UAH"
                ),
                gross_amount=(
                    D("12001.00")
                    if scenario == "gross_mismatch"
                    and month_number == 8
                    else D("12000.00")
                ),
                calculated_by=actor_id,
            )
            db.add(calculation)
            await db.flush()

            db.add(
                PayrollCalculationLine(
                    company_id=company_id,
                    payroll_calculation_id=calculation.id,
                    line_no=1,
                    line_type="salary",
                    description="Verified E2E monthly salary",
                    quantity=D("1"),
                    rate=D("12000"),
                    amount=D("12000.00"),
                    currency_code="UAH",
                )
            )

        await db.flush()

        if scenario == "unverified_minimum_wage":
            assert month_number == 8
            assert calculation.payroll_period_id == period.id
            assert period.month == 8

            calculation.gross_amount = D("12500.00")

            db.add(
                PayrollCalculationLine(
                    company_id=company_id,
                    payroll_calculation_id=calculation.id,
                    line_no=2,
                    line_type="minimum_wage",
                    description="Unverified top-up negative E2E",
                    quantity=D("1"),
                    rate=D("500.00"),
                    amount=D("500.00"),
                    currency_code="UAH",
                    source_effective_from=period.start_date,
                    source_effective_to=period.end_date,
                )
            )
            await db.flush()

        if scenario != "positive":
            expected_errors = {'unfinalized_period': 'Finalized complete payroll month', 'wrong_currency': 'Current UAH payroll calculation', 'gross_mismatch': 'Salary lines do not reconcile', 'attendance_mismatch': 'Attendance does not reconcile', 'unverified_minimum_wage': 'Minimum-wage'}
            with pytest.raises(
                SeveranceAverageError,
                match=expected_errors[scenario],
            ):
                await derive_database_severance_average(
                    db,
                    company_id=company_id,
                    employment_contract_id=contract.id,
                    termination_date=date(2026, 9, 30),
                )
            await db.rollback()
            print(f"NEGATIVE {scenario.upper()} = BLOCKED")
            return

        result = await derive_database_severance_average(
            db,
            company_id=company_id,
            employment_contract_id=contract.id,
            termination_date=date(2026, 9, 30),
        )

        expected = (
            D("24000") / D(total_days)
            * (D(total_days) / D("2"))
        ).quantize(D("0.01"))

        assert result.average_monthly == expected
        assert result.average_monthly == D("12000.00")
        assert result.reference_start == first
        assert result.reference_end == last
        assert "revision 1" in result.source_reference

        print("TWO FINALIZED MONTHS = PASS")
        print("ATTENDANCE AND SCHEDULE = PASS")
        print("CURRENT PAYROLL REVISIONS = PASS")
        print("AVERAGE MONTHLY = 12000.00 UAH")

        await db.rollback()
