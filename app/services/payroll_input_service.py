from datetime import date, timedelta
from decimal import Decimal

from app.services.payroll_mutation_guard import serialized_payroll_mutation

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.employment_contract import (
    EmploymentContract,
    EmploymentContractStatus,
)
from app.models.payroll import (
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollInputSource,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.time_attendance import (
    AttendanceRecord,
    AttendanceStatus,
    EmploymentScheduleAssignment,
    WorkScheduleDay,
)
from app.schemas.payroll import PayrollInputCreate, PayrollInputUpdate


class PayrollInputError(Exception):
    pass


class PayrollInputNotFoundError(PayrollInputError):
    pass


class PayrollInputConflictError(PayrollInputError):
    pass


class PayrollInputLifecycleError(PayrollInputError):
    pass


class PayrollInputDerivationError(PayrollInputError):
    pass


async def _require_payroll_period(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
    lock: bool = False,
) -> PayrollPeriod:
    stmt = select(PayrollPeriod).where(
        PayrollPeriod.company_id == company_id,
        PayrollPeriod.id == payroll_period_id,
    )

    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)

    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        raise PayrollInputNotFoundError(
            "Payroll period not found"
        )

    return row


async def _require_contract(
    db: AsyncSession,
    *,
    company_id: int,
    employment_contract_id: int,
) -> EmploymentContract:
    result = await db.execute(
        select(EmploymentContract).execution_options(populate_existing=True).where(
            EmploymentContract.company_id == company_id,
            EmploymentContract.id == employment_contract_id,
        )
    )

    row = result.scalar_one_or_none()

    if row is None:
        raise PayrollInputNotFoundError(
            "Employment contract not found"
        )

    return row


async def _require_payroll_input(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
    lock: bool = False,
) -> PayrollInput:
    stmt = select(PayrollInput).where(
        PayrollInput.company_id == company_id,
        PayrollInput.id == payroll_input_id,
    )

    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)

    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        raise PayrollInputNotFoundError(
            "Payroll input not found"
        )

    return row


def _contract_period_intersection(
    contract: EmploymentContract,
    period: PayrollPeriod,
) -> tuple[date, date]:
    if contract.status == EmploymentContractStatus.CANCELLED:
        raise PayrollInputLifecycleError(
            "Cancelled employment contract cannot receive payroll input"
        )

    start_date = max(
        contract.start_date,
        period.start_date,
    )

    end_date = period.end_date

    if contract.end_date is not None:
        end_date = min(
            end_date,
            contract.end_date,
        )

    if end_date < start_date:
        raise PayrollInputLifecycleError(
            "Employment contract does not intersect payroll period"
        )

    return start_date, end_date


def _scheduled_minutes_for_day(
    schedule_day: WorkScheduleDay,
) -> int:
    if not schedule_day.is_working_day:
        return 0

    if (
        schedule_day.start_time is None
        or schedule_day.end_time is None
    ):
        raise PayrollInputDerivationError(
            "Working schedule day has no working interval"
        )

    start_seconds = (
        schedule_day.start_time.hour * 3600
        + schedule_day.start_time.minute * 60
        + schedule_day.start_time.second
    )

    end_seconds = (
        schedule_day.end_time.hour * 3600
        + schedule_day.end_time.minute * 60
        + schedule_day.end_time.second
    )

    gross_minutes = (end_seconds - start_seconds) // 60

    net_minutes = gross_minutes - schedule_day.break_minutes

    if net_minutes < 0:
        raise PayrollInputDerivationError(
            "Schedule break exceeds working interval"
        )

    return net_minutes


async def _load_schedule_assignments(
    db: AsyncSession,
    *,
    company_id: int,
    employment_contract_id: int,
    date_from: date,
    date_to: date,
) -> list[EmploymentScheduleAssignment]:
    result = await db.execute(
        select(EmploymentScheduleAssignment).execution_options(populate_existing=True)
        .where(
            EmploymentScheduleAssignment.company_id == company_id,
            EmploymentScheduleAssignment.employment_contract_id
            == employment_contract_id,
            EmploymentScheduleAssignment.effective_from <= date_to,
            (
                EmploymentScheduleAssignment.effective_to.is_(None)
                | (
                    EmploymentScheduleAssignment.effective_to
                    >= date_from
                )
            ),
        )
        .order_by(
            EmploymentScheduleAssignment.effective_from,
            EmploymentScheduleAssignment.id,
        )
    )

    return list(result.scalars().all())


async def _load_schedule_days(
    db: AsyncSession,
    *,
    company_id: int,
    schedule_ids: set[int],
) -> dict[tuple[int, int], WorkScheduleDay]:
    if not schedule_ids:
        return {}

    result = await db.execute(
        select(WorkScheduleDay).execution_options(populate_existing=True).where(
            WorkScheduleDay.company_id == company_id,
            WorkScheduleDay.work_schedule_id.in_(schedule_ids),
        )
    )

    rows = list(result.scalars().all())

    return {
        (row.work_schedule_id, row.weekday): row
        for row in rows
    }


async def _load_attendance(
    db: AsyncSession,
    *,
    company_id: int,
    employment_contract_id: int,
    date_from: date,
    date_to: date,
) -> dict[date, AttendanceRecord]:
    result = await db.execute(
        select(AttendanceRecord).execution_options(populate_existing=True).where(
            AttendanceRecord.company_id == company_id,
            AttendanceRecord.employment_contract_id
            == employment_contract_id,
            AttendanceRecord.work_date >= date_from,
            AttendanceRecord.work_date <= date_to,
        )
    )

    return {
        row.work_date: row
        for row in result.scalars().all()
    }


def _assignment_for_day(
    assignments: list[EmploymentScheduleAssignment],
    work_date: date,
) -> EmploymentScheduleAssignment | None:
    matches = [
        row
        for row in assignments
        if (
            row.effective_from <= work_date
            and (
                row.effective_to is None
                or row.effective_to >= work_date
            )
        )
    ]

    if len(matches) > 1:
        raise PayrollInputDerivationError(
            "Overlapping schedule assignments detected"
        )

    return matches[0] if matches else None


async def _derive_monthly_norm(db, *, company_id, contract, period):
    from app.models.payroll_opening import PayrollEarningsHistory
    imported=await db.scalar(select(PayrollEarningsHistory.id).where(
        PayrollEarningsHistory.company_id==company_id,
        PayrollEarningsHistory.employment_contract_id==contract.id,
        PayrollEarningsHistory.month>=period.start_date.replace(day=1),
        PayrollEarningsHistory.month<=period.end_date).limit(1))
    if imported is not None:
        raise PayrollInputDerivationError('Payroll period overlaps imported earnings history')
    assignments = await _load_schedule_assignments(db, company_id=company_id,
        employment_contract_id=contract.id, date_from=period.start_date,
        date_to=period.end_date)
    if not assignments:
        raise PayrollInputDerivationError('Monthly payroll requires an assigned work schedule')
    assignments = sorted(assignments, key=lambda row: row.effective_from)
    days = await _load_schedule_days(db, company_id=company_id,
        schedule_ids={row.work_schedule_id for row in assignments})
    total = 0
    current = period.start_date
    while current <= period.end_date:
        assignment = _assignment_for_day(assignments, current)
        if assignment is None:
            if current < contract.start_date:
                assignment = assignments[0]
            elif contract.end_date is not None and current > contract.end_date:
                assignment = assignments[-1]
            else:
                raise PayrollInputDerivationError('Work schedule has a gap in the payroll month')
        day = days.get((assignment.work_schedule_id, current.isoweekday()))
        if day is not None:
            total += _scheduled_minutes_for_day(day)
        current += timedelta(days=1)
    return total


async def _derive_time_quantities(
    db: AsyncSession,
    *,
    company_id: int,
    employment_contract_id: int,
    date_from: date,
    date_to: date,
) -> tuple[int, int, int, int]:
    assignments = await _load_schedule_assignments(
        db,
        company_id=company_id,
        employment_contract_id=employment_contract_id,
        date_from=date_from,
        date_to=date_to,
    )

    schedule_ids = {
        row.work_schedule_id
        for row in assignments
    }

    schedule_days = await _load_schedule_days(
        db,
        company_id=company_id,
        schedule_ids=schedule_ids,
    )

    attendance = await _load_attendance(
        db,
        company_id=company_id,
        employment_contract_id=employment_contract_id,
        date_from=date_from,
        date_to=date_to,
    )

    scheduled_minutes = 0
    worked_minutes = 0
    leave_days = 0
    sick_days = 0

    current = date_from

    while current <= date_to:
        assignment = _assignment_for_day(
            assignments,
            current,
        )

        schedule_day = None

        if assignment is not None:
            schedule_day = schedule_days.get(
                (
                    assignment.work_schedule_id,
                    current.isoweekday(),
                )
            )

        is_scheduled_working_day = (
            schedule_day is not None
            and schedule_day.is_working_day
        )

        if schedule_day is not None:
            scheduled_minutes += _scheduled_minutes_for_day(
                schedule_day
            )

        attendance_row = attendance.get(current)

        if attendance_row is not None:
            worked_minutes += attendance_row.worked_minutes

            if is_scheduled_working_day:
                if attendance_row.status == AttendanceStatus.LEAVE:
                    leave_days += 1
                elif attendance_row.status == AttendanceStatus.SICK:
                    sick_days += 1

        current += timedelta(days=1)

    return (
        scheduled_minutes,
        worked_minutes,
        leave_days,
        sick_days,
    )


async def _load_salary_rates(
    db: AsyncSession,
    *,
    company_id: int,
    employment_contract_id: int,
    date_from: date,
    date_to: date,
) -> list[EmployeeSalaryRate]:
    result = await db.execute(
        select(EmployeeSalaryRate).execution_options(populate_existing=True)
        .where(
            EmployeeSalaryRate.company_id == company_id,
            EmployeeSalaryRate.employment_contract_id
            == employment_contract_id,
            EmployeeSalaryRate.effective_from <= date_to,
            (
                EmployeeSalaryRate.effective_to.is_(None)
                | (
                    EmployeeSalaryRate.effective_to
                    >= date_from
                )
            ),
        )
        .order_by(
            EmployeeSalaryRate.effective_from,
            EmployeeSalaryRate.id,
        )
    )

    return list(result.scalars().all())


def _salary_slice_bounds(
    rate: EmployeeSalaryRate,
    *,
    date_from: date,
    date_to: date,
) -> tuple[date, date]:
    start_date = max(
        rate.effective_from,
        date_from,
    )

    end_date = date_to

    if rate.effective_to is not None:
        end_date = min(
            end_date,
            rate.effective_to,
        )

    return start_date, end_date


def _validate_salary_coverage(
    rates: list[EmployeeSalaryRate],
    *,
    date_from: date,
    date_to: date,
) -> list[
    tuple[
        EmployeeSalaryRate,
        date,
        date,
    ]
]:
    slices = []

    for rate in rates:
        slice_from, slice_to = _salary_slice_bounds(
            rate,
            date_from=date_from,
            date_to=date_to,
        )

        if slice_to >= slice_from:
            slices.append(
                (
                    rate,
                    slice_from,
                    slice_to,
                )
            )

    slices.sort(
        key=lambda item: (
            item[1],
            item[0].id,
        )
    )

    if not slices:
        raise PayrollInputDerivationError(
            "No salary rate covers payroll input period"
        )

    expected_date = date_from

    for rate, slice_from, slice_to in slices:
        if slice_from < expected_date:
            raise PayrollInputDerivationError(
                "Overlapping salary rates detected"
            )

        if slice_from > expected_date:
            raise PayrollInputDerivationError(
                "Salary rate coverage has a gap"
            )

        expected_date = slice_to + timedelta(days=1)

    if expected_date <= date_to:
        raise PayrollInputDerivationError(
            "Salary rate coverage has a gap"
        )

    return slices


async def _replace_salary_slices(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
    employment_contract_id: int,
    date_from: date,
    date_to: date,
) -> None:
    rates = await _load_salary_rates(
        db,
        company_id=company_id,
        employment_contract_id=employment_contract_id,
        date_from=date_from,
        date_to=date_to,
    )

    slices = _validate_salary_coverage(
        rates,
        date_from=date_from,
        date_to=date_to,
    )

    await db.execute(
        delete(PayrollInputSalarySlice).where(
            PayrollInputSalarySlice.company_id == company_id,
            PayrollInputSalarySlice.payroll_input_id
            == payroll_input_id,
        )
    )

    for rate, slice_from, slice_to in slices:
        (
            slice_scheduled_minutes,
            slice_worked_minutes,
            _slice_leave_days,
            _slice_sick_days,
        ) = await _derive_time_quantities(
            db,
            company_id=company_id,
            employment_contract_id=employment_contract_id,
            date_from=slice_from,
            date_to=slice_to,
        )

        db.add(
            PayrollInputSalarySlice(
                company_id=company_id,
                payroll_input_id=payroll_input_id,
                salary_rate_id=rate.id,
                salary_rate_type=rate.rate_type,
                salary_rate_amount=rate.amount,
                currency_code=rate.currency_code,
                effective_from=slice_from,
                effective_to=slice_to,
                scheduled_minutes=slice_scheduled_minutes,
                worked_minutes=slice_worked_minutes,
            )
        )


async def _ensure_unique_payroll_input(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
    employment_contract_id: int,
) -> None:
    result = await db.execute(
        select(PayrollInput.id)
        .where(
            PayrollInput.company_id == company_id,
            PayrollInput.payroll_period_id == payroll_period_id,
            PayrollInput.employment_contract_id
            == employment_contract_id,
        )
        .limit(1)
    )

    if result.scalar_one_or_none() is not None:
        raise PayrollInputConflictError(
            "Payroll input already exists for period and contract"
        )


async def list_payroll_inputs(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
    contract_id: int | None = None,
) -> list[PayrollInput]:
    await _require_payroll_period(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    result = await db.execute(
        select(PayrollInput)
        .where(
            PayrollInput.company_id == company_id,
            PayrollInput.payroll_period_id == payroll_period_id,
            PayrollInput.employment_contract_id == contract_id if contract_id is not None else True,
        )
        .order_by(
            PayrollInput.employment_contract_id,
            PayrollInput.id,
        )
    )

    return list(result.scalars().all())


async def get_payroll_input(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
) -> PayrollInput:
    return await _require_payroll_input(
        db,
        company_id=company_id,
        payroll_input_id=payroll_input_id,
    )


async def list_salary_slices(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
) -> list[PayrollInputSalarySlice]:
    await _require_payroll_input(
        db,
        company_id=company_id,
        payroll_input_id=payroll_input_id,
    )

    result = await db.execute(
        select(PayrollInputSalarySlice)
        .where(
            PayrollInputSalarySlice.company_id == company_id,
            PayrollInputSalarySlice.payroll_input_id
            == payroll_input_id,
        )
        .order_by(
            PayrollInputSalarySlice.effective_from,
            PayrollInputSalarySlice.id,
        )
    )

    return list(result.scalars().all())


@serialized_payroll_mutation(PayrollInputLifecycleError)
async def create_payroll_input(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
    created_by: int,
    data: PayrollInputCreate,
) -> PayrollInput:
    period = await _require_payroll_period(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        lock=True,
    )

    if period.status != PayrollPeriodStatus.DRAFT:
        raise PayrollInputLifecycleError(
            "Payroll inputs can only be created in draft periods"
        )

    contract = await _require_contract(
        db,
        company_id=company_id,
        employment_contract_id=data.employment_contract_id,
    )

    date_from, date_to = _contract_period_intersection(
        contract,
        period,
    )

    await _ensure_unique_payroll_input(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        employment_contract_id=data.employment_contract_id,
    )

    monthly_norm_minutes = await _derive_monthly_norm(db, company_id=company_id,
        contract=contract, period=period)

    (
        scheduled_minutes,
        worked_minutes,
        leave_days,
        sick_days,
    ) = await _derive_time_quantities(
        db,
        company_id=company_id,
        employment_contract_id=data.employment_contract_id,
        date_from=date_from,
        date_to=date_to,
    )

    row = PayrollInput(
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        employment_contract_id=data.employment_contract_id,
        monthly_norm_minutes=monthly_norm_minutes,
        scheduled_minutes=scheduled_minutes,
        worked_minutes=worked_minutes,
        leave_days=leave_days,
        sick_days=sick_days,
        manual_adjustment_amount=data.manual_adjustment_amount,
        manual_adjustment_reason=data.manual_adjustment_reason,
        source=(
            PayrollInputSource.MANUAL
            if data.manual_adjustment_amount != Decimal("0")
            else PayrollInputSource.SYSTEM
        ),
        created_by=created_by,
    )

    db.add(row)
    await db.flush()

    await _replace_salary_slices(
        db,
        company_id=company_id,
        payroll_input_id=row.id,
        employment_contract_id=row.employment_contract_id,
        date_from=date_from,
        date_to=date_to,
    )

    await db.flush()
    await db.refresh(row)

    return row


@serialized_payroll_mutation(PayrollInputLifecycleError)
async def refresh_payroll_input(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
) -> PayrollInput:
    row = await _require_payroll_input(
        db,
        company_id=company_id,
        payroll_input_id=payroll_input_id,
        lock=True,
    )

    period = await _require_payroll_period(
        db,
        company_id=company_id,
        payroll_period_id=row.payroll_period_id,
        lock=True,
    )

    if period.status != PayrollPeriodStatus.DRAFT:
        raise PayrollInputLifecycleError(
            "Finalized payroll inputs cannot be refreshed"
        )

    contract = await _require_contract(
        db,
        company_id=company_id,
        employment_contract_id=row.employment_contract_id,
    )

    date_from, date_to = _contract_period_intersection(
        contract,
        period,
    )

    monthly_norm_minutes = await _derive_monthly_norm(db, company_id=company_id,
        contract=contract, period=period)

    (
        scheduled_minutes,
        worked_minutes,
        leave_days,
        sick_days,
    ) = await _derive_time_quantities(
        db,
        company_id=company_id,
        employment_contract_id=row.employment_contract_id,
        date_from=date_from,
        date_to=date_to,
    )

    row.monthly_norm_minutes = monthly_norm_minutes
    row.scheduled_minutes = scheduled_minutes
    row.worked_minutes = worked_minutes
    row.leave_days = leave_days
    row.sick_days = sick_days

    await _replace_salary_slices(
        db,
        company_id=company_id,
        payroll_input_id=row.id,
        employment_contract_id=row.employment_contract_id,
        date_from=date_from,
        date_to=date_to,
    )

    await db.flush()
    await db.refresh(row)

    return row


@serialized_payroll_mutation(PayrollInputLifecycleError)
async def update_payroll_input_adjustment(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
    data: PayrollInputUpdate,
) -> PayrollInput:
    row = await _require_payroll_input(
        db,
        company_id=company_id,
        payroll_input_id=payroll_input_id,
        lock=True,
    )

    period = await _require_payroll_period(
        db,
        company_id=company_id,
        payroll_period_id=row.payroll_period_id,
        lock=True,
    )

    if period.status != PayrollPeriodStatus.DRAFT:
        raise PayrollInputLifecycleError(
            "Finalized payroll inputs cannot be changed"
        )

    values = data.model_dump(exclude_unset=True)

    next_amount = values.get(
        "manual_adjustment_amount",
        row.manual_adjustment_amount,
    )

    next_reason = values.get(
        "manual_adjustment_reason",
        row.manual_adjustment_reason,
    )

    if (
        next_amount != Decimal("0")
        and (
            next_reason is None
            or not next_reason.strip()
        )
    ):
        raise PayrollInputConflictError(
            "manual_adjustment_reason is required "
            "when manual_adjustment_amount is non-zero"
        )

    row.manual_adjustment_amount = next_amount
    row.manual_adjustment_reason = (
        next_reason.strip()
        if next_reason is not None
        and next_reason.strip()
        else None
    )

    row.source = (
        PayrollInputSource.MANUAL
        if row.manual_adjustment_amount != Decimal("0")
        else PayrollInputSource.SYSTEM
    )

    await db.flush()
    await db.refresh(row)

    return row
