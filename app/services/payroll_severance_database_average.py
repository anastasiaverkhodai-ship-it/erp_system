"""Read-only, fail-closed ERP source for ordinary severance averages.

Supports only full two-month service, salary-only UAH payroll and
verified schedule/attendance. Other Order 100 cases require separately
validated handling. It neither creates severance nor posts payroll.
"""

from calendar import monthrange
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.employment_contract import EmploymentContract
from app.models.payroll import (
    PayrollPeriod,
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInput,
)
from app.models.payroll_opening import PayrollEarningsHistory
from app.models.time_attendance import AttendanceRecord

from app.services.payroll_revision_service import (
    current_calculation_filter,
)
from app.services.payroll_input_service import (
    _load_schedule_assignments,
    _load_schedule_days,
    _assignment_for_day,
    _scheduled_minutes_for_day,
)

from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
)
from app.services.payroll_severance_monthly_sources import (
    SeveranceMonthlyEvidence,
    preceding_two_months,
    derive_average_from_monthly_evidence,
)


def _value(value):
    return getattr(value, "value", value)


def _require(condition, message):
    if not condition:
        raise SeveranceAverageError(message)


def _validated_month(
    *,
    period,
    calculation,
    payroll_input,
    lines,
    attendance,
    schedule_minutes,
    month,
    month_end,
    verified_minimum_wage_amount=None,
):
    """Independently reconcile a full payroll month's evidence."""
    _require(
        _value(period.status) == "finalized"
        and period.start_date == month
        and period.end_date == month_end,
        "Finalized complete payroll month required",
    )

    _require(
        calculation.currency_code == "UAH"
        and _value(calculation.status) == "calculated",
        "Current UAH payroll calculation required",
    )

    _require(
        calculation.payroll_input_id == payroll_input.id
        and calculation.payroll_period_id == period.id,
        "Payroll source association mismatch",
    )

    from app.services.payroll_severance_order100_classifier import (
        Order100ClassificationError,
        require_supported_severance_lines,
    )

    try:
        earnings = require_supported_severance_lines(
            lines,
            gross_amount=Decimal(calculation.gross_amount),
            verified_minimum_wage_amount=verified_minimum_wage_amount,
        )
    except Order100ClassificationError as exc:
        raise SeveranceAverageError(str(exc)) from exc

    _require(
        earnings == Decimal(calculation.gross_amount)
        and earnings > 0,
        "Salary lines do not reconcile to payroll gross",
    )

    observed = {}
    for item in attendance:
        day = item.work_date
        _require(
            month <= day <= month_end and day not in observed,
            "Missing uniqueness in dated attendance evidence",
        )
        _require(
            _value(item.status) != "absent",
            "Unclassified absence requires legal review",
        )
        _require(
            type(item.worked_minutes) is int
            and item.worked_minutes >= 0,
            "Invalid attendance minutes",
        )
        observed[day] = item.worked_minutes

    worked_days = Decimal(
        sum(minutes > 0 for minutes in observed.values())
    )

    worked_minutes = sum(observed.values())

    _require(
        worked_minutes == payroll_input.worked_minutes,
        "Attendance does not reconcile to payroll input",
    )

    _require(
        schedule_minutes
        and all(
            type(minutes) is int and minutes >= 0
            for minutes in schedule_minutes.values()
        ),
        "Complete dated work schedule required",
    )

    scheduled_days = Decimal(
        sum(minutes > 0 for minutes in schedule_minutes.values())
    )

    _require(
        worked_days > 0
        and scheduled_days > 0
        and worked_days <= scheduled_days,
        "Worked days do not reconcile to scheduled days",
    )

    _require(
        all(
            day in schedule_minutes
            and (
                minutes == 0
                or schedule_minutes[day] > 0
            )
            for day, minutes in observed.items()
        ),
        "Attendance outside verified schedule",
    )

    _require(
        all(
            day in observed and observed[day] > 0
            for day, minutes in schedule_minutes.items()
            if minutes > 0
        ),
        "Missing worked day or unclassified absence",
    )

    return earnings, worked_days, scheduled_days


async def derive_database_severance_average(
    db,
    *,
    company_id: int,
    employment_contract_id: int,
    termination_date,
):
    """Derive evidence exclusively through company-scoped DB reads."""
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(employment_contract_id) is not int
        or employment_contract_id <= 0
    ):
        raise SeveranceAverageError(
            "Valid company and contract identifiers required"
        )

    start, second, end = preceding_two_months(
        termination_date
    )

    contract = await db.scalar(
        select(EmploymentContract).where(
            EmploymentContract.company_id == company_id,
            EmploymentContract.id == employment_contract_id,
        )
    )

    _require(
        contract is not None,
        "Company-scoped employment contract not found",
    )
    _require(
        contract.start_date <= start
        and (
            contract.end_date is None
            or contract.end_date >= end
        ),
        "Short or interrupted employment requires separate Order 100 handling",
    )

    # Earlier validated rule windows do not authorize all possible
    # historical/future payroll dates.
    _require(
        start.year >= 2026 and end.year <= 2026,
        "Reference period requires a dated legal rule review",
    )

    imported = await db.scalar(
        select(PayrollEarningsHistory.id).where(
            PayrollEarningsHistory.company_id == company_id,
            PayrollEarningsHistory.employment_contract_id
            == employment_contract_id,
            PayrollEarningsHistory.month >= start,
            PayrollEarningsHistory.month <= end,
        ).limit(1)
    )

    _require(
        imported is None,
        "Imported history lacks verified severance workday evidence",
    )

    evidence = []

    for month in (start, second):
        month_end = month.replace(
            day=monthrange(month.year, month.month)[1]
        )

        rows = (
            await db.execute(
                select(
                    PayrollCalculation,
                    PayrollPeriod,
                    PayrollInput,
                )
                .join(
                    PayrollPeriod,
                    (PayrollPeriod.company_id
                     == PayrollCalculation.company_id)
                    & (PayrollPeriod.id
                       == PayrollCalculation.payroll_period_id),
                )
                .join(
                    PayrollInput,
                    (PayrollInput.company_id
                     == PayrollCalculation.company_id)
                    & (PayrollInput.id
                       == PayrollCalculation.payroll_input_id),
                )
                .where(
                    PayrollCalculation.company_id == company_id,
                    PayrollCalculation.employment_contract_id
                    == employment_contract_id,
                    PayrollInput.employment_contract_id
                    == employment_contract_id,
                    PayrollPeriod.start_date == month,
                    PayrollPeriod.end_date == month_end,
                    current_calculation_filter(),
                )
            )
        ).all()

        _require(
            len(rows) == 1,
            "Exactly one current monthly payroll calculation required",
        )

        calculation, period, payroll_input = rows[0]

        lines = list((
            await db.scalars(
                select(PayrollCalculationLine).where(
                    PayrollCalculationLine.company_id == company_id,
                    PayrollCalculationLine.payroll_calculation_id
                    == calculation.id,
                )
            )
        ).all())

        attendance = list((
            await db.scalars(
                select(AttendanceRecord).where(
                    AttendanceRecord.company_id == company_id,
                    AttendanceRecord.employment_contract_id
                    == employment_contract_id,
                    AttendanceRecord.work_date >= month,
                    AttendanceRecord.work_date <= month_end,
                )
            )
        ).all())

        assignments = await _load_schedule_assignments(
            db,
            company_id=company_id,
            employment_contract_id=employment_contract_id,
            date_from=month,
            date_to=month_end,
        )

        _require(
            bool(assignments),
            "Verified monthly schedule assignment required",
        )

        days = await _load_schedule_days(
            db,
            company_id=company_id,
            schedule_ids={
                row.work_schedule_id for row in assignments
            },
        )

        schedule_minutes = {}
        cursor = month

        while cursor <= month_end:
            assignment = _assignment_for_day(
                assignments, cursor
            )
            _require(
                assignment is not None,
                "Schedule assignment gap",
            )
            schedule_day = days.get(
                (assignment.work_schedule_id, cursor.isoweekday())
            )
            if schedule_day is None:
                _require(
                    cursor.isoweekday() in (6, 7)
                    and all(
                        (assignment.work_schedule_id, wd) in days
                        and days[
                            (assignment.work_schedule_id, wd)
                        ].is_working_day
                        for wd in range(1, 6)
                    )
                    and not any(
                        (assignment.work_schedule_id, wd) in days
                        for wd in (6, 7)
                    ),
                    "Undocumented schedule weekday",
                )
                schedule_minutes[cursor] = 0
            else:
                schedule_minutes[cursor] = (
                    _scheduled_minutes_for_day(schedule_day)
                )
            cursor += timedelta(days=1)

        # A minimum-wage component must be proven against the current
        # payroll revision before it can enter the Order 100 average.
        # This does not grant financial authorization.
        verified_minimum_wage_amount = None

        if any(
            getattr(line.line_type, "value", line.line_type)
            == "minimum_wage"
            for line in lines
        ):
            from app.services.payroll_severance_minimum_wage_provenance import (
                SeveranceMinimumWageProvenanceError,
                derive_verified_earnings_candidate,
            )

            try:
                candidate = await derive_verified_earnings_candidate(
                    db,
                    company_id=company_id,
                    payroll_calculation_id=calculation.id,
                )
            except SeveranceMinimumWageProvenanceError as exc:
                raise SeveranceAverageError(
                    "Minimum-wage historical payroll proof failed: "
                    + str(exc)
                ) from exc

            _require(
                candidate.payroll_calculation_id == calculation.id
                and candidate.revision == calculation.revision
                and candidate.gross_amount
                == Decimal(calculation.gross_amount),
                "Verified minimum-wage source does not match current payroll",
            )

            verified_minimum_wage_amount = (
                candidate.minimum_wage_amount
            )

            from app.services.payroll_severance_order100_minimum_eligibility import (
                Order100MinimumEligibilityError,
                authorize_verified_minimum_wage_for_average,
            )

            try:
                eligibility = authorize_verified_minimum_wage_for_average(
                    candidate=candidate,
                    calculation=calculation,
                    period=period,
                    lines=lines,
                )
            except Order100MinimumEligibilityError as exc:
                raise SeveranceAverageError(
                    "Minimum-wage Order 100 eligibility failed: "
                    + str(exc)
                ) from exc

            _require(
                eligibility.eligible_for_average is True
                and eligibility.financial_execution_allowed is False
                and eligibility.minimum_wage_amount
                == verified_minimum_wage_amount,
                "Minimum-wage Order 100 eligibility not established",
            )

            # Do not use candidate.financial_execution_allowed as
            # proof of statutory inclusion. Calculation eligibility
            # and authorization to execute a payment are separate.

        earnings, worked, scheduled = _validated_month(
            period=period,
            calculation=calculation,
            payroll_input=payroll_input,
            lines=lines,
            attendance=attendance,
            schedule_minutes=schedule_minutes,
            month=month,
            month_end=month_end,
            verified_minimum_wage_amount=verified_minimum_wage_amount,
        )

        evidence.append(
            SeveranceMonthlyEvidence(
                month=month,
                eligible_earnings=earnings,
                worked_days=worked,
                scheduled_days=scheduled,
                source_type="verified_payroll_severance_basis",
                source_id=calculation.id,
                source_reference=(
                    f"Finalized ERP calculation {calculation.id}, "
                    f"revision {calculation.revision}; "
                    f"attendance/schedule reconciled"
                ),
                company_id=company_id,
                employment_contract_id=employment_contract_id,
                verified_finalized=True,
            )
        )

    return derive_average_from_monthly_evidence(
        company_id=company_id,
        employment_contract_id=employment_contract_id,
        termination_date=termination_date,
        evidence=evidence,
    )
