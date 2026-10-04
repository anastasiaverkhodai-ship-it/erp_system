from datetime import datetime, timedelta, timezone

from app.services.payroll_mutation_guard import serialized_payroll_mutation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import (
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.schemas.payroll import PayrollPeriodCreate


class PayrollPeriodError(Exception):
    pass


class PayrollPeriodNotFoundError(PayrollPeriodError):
    pass


class PayrollPeriodConflictError(PayrollPeriodError):
    pass


class PayrollPeriodLifecycleError(PayrollPeriodError):
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
    payroll_period = result.scalar_one_or_none()

    if payroll_period is None:
        raise PayrollPeriodNotFoundError(
            "Payroll period not found"
        )

    return payroll_period


async def _ensure_unique_period(
    db: AsyncSession,
    *,
    company_id: int,
    year: int,
    month: int,
) -> None:
    stmt = (
        select(PayrollPeriod.id)
        .where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.year == year,
            PayrollPeriod.month == month,
        )
        .limit(1)
    )

    result = await db.execute(stmt)

    if result.scalar_one_or_none() is not None:
        raise PayrollPeriodConflictError(
            "Payroll period already exists for company, year and month"
        )


async def list_payroll_periods(
    db: AsyncSession,
    *,
    company_id: int,
    status: PayrollPeriodStatus | None = None,
    year: int | None = None,
) -> list[PayrollPeriod]:
    conditions = [
        PayrollPeriod.company_id == company_id,
    ]

    if status is not None:
        conditions.append(
            PayrollPeriod.status == status
        )

    if year is not None:
        conditions.append(
            PayrollPeriod.year == year
        )

    stmt = (
        select(PayrollPeriod)
        .where(*conditions)
        .order_by(
            PayrollPeriod.year.desc(),
            PayrollPeriod.month.desc(),
            PayrollPeriod.id.desc(),
        )
    )

    result = await db.execute(stmt)

    return list(result.scalars().all())


async def get_payroll_period(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
) -> PayrollPeriod:
    return await _require_payroll_period(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )


@serialized_payroll_mutation(PayrollPeriodLifecycleError)
async def create_payroll_period(
    db: AsyncSession,
    *,
    company_id: int,
    created_by: int,
    data: PayrollPeriodCreate,
) -> PayrollPeriod:
    await _ensure_unique_period(
        db,
        company_id=company_id,
        year=data.year,
        month=data.month,
    )

    payroll_period = PayrollPeriod(
        company_id=company_id,
        year=data.year,
        month=data.month,
        start_date=data.start_date,
        end_date=data.end_date,
        status=PayrollPeriodStatus.DRAFT,
        created_by=created_by,
        finalized_by=None,
        finalized_at=None,
    )

    db.add(payroll_period)

    try:
        await db.flush()
    except IntegrityError as exc:
        raise PayrollPeriodConflictError(
            "Payroll period conflicts with existing data"
        ) from exc

    await db.refresh(payroll_period)

    return payroll_period


async def _validate_payroll_input_for_finalization(
    db: AsyncSession,
    *,
    payroll_period: PayrollPeriod,
    payroll_input: "PayrollInput",
) -> None:
    from app.models.employment_contract import EmploymentContract
    from app.models.payroll import PayrollInputSalarySlice

    contract_result = await db.execute(
        select(EmploymentContract).where(
            EmploymentContract.company_id
            == payroll_period.company_id,
            EmploymentContract.id
            == payroll_input.employment_contract_id,
        )
    )

    contract = contract_result.scalar_one_or_none()

    if contract is None:
        raise PayrollPeriodLifecycleError(
            "Payroll input employment contract not found"
        )

    from app.services.payroll_input_service import (
        _load_schedule_assignments, _load_schedule_days, _load_attendance,
        _assignment_for_day, _scheduled_minutes_for_day,
    )
    date_from = max(
        payroll_period.start_date,
        contract.start_date,
    )

    date_to = payroll_period.end_date

    if contract.end_date is not None:
        date_to = min(
            date_to,
            contract.end_date,
        )

    if date_to < date_from:
        raise PayrollPeriodLifecycleError(
            "Payroll input contract does not intersect payroll period"
        )

    assignments = await _load_schedule_assignments(db, company_id=payroll_period.company_id,
        employment_contract_id=contract.id, date_from=date_from, date_to=date_to)
    days = await _load_schedule_days(db, company_id=payroll_period.company_id,
        schedule_ids={row.work_schedule_id for row in assignments})
    attendance = await _load_attendance(db, company_id=payroll_period.company_id,
        employment_contract_id=contract.id, date_from=date_from, date_to=date_to)
    current = date_from
    while current <= date_to:
        assignment = _assignment_for_day(assignments, current)
        if assignment is None:
            raise PayrollPeriodLifecycleError(f'Missing work schedule on {current}')
        day = days.get((assignment.work_schedule_id, current.isoweekday()))
        if day is not None and _scheduled_minutes_for_day(day) > 0 and current not in attendance:
            raise PayrollPeriodLifecycleError(f'Missing attendance on {current}')
        current += timedelta(days=1)

    slice_result = await db.execute(
        select(PayrollInputSalarySlice)
        .where(
            PayrollInputSalarySlice.company_id
            == payroll_period.company_id,
            PayrollInputSalarySlice.payroll_input_id
            == payroll_input.id,
        )
        .order_by(
            PayrollInputSalarySlice.effective_from,
            PayrollInputSalarySlice.id,
        )
    )

    slices = list(slice_result.scalars().all())

    if not slices:
        raise PayrollPeriodLifecycleError(
            "Payroll input has no salary-rate snapshot"
        )

    expected_date = date_from

    for salary_slice in slices:
        if salary_slice.effective_from != expected_date:
            raise PayrollPeriodLifecycleError(
                "Payroll input salary-rate snapshot has a gap or overlap"
            )

        if salary_slice.effective_to < salary_slice.effective_from:
            raise PayrollPeriodLifecycleError(
                "Payroll input salary-rate snapshot has invalid dates"
            )

        expected_date = (
            salary_slice.effective_to
            + timedelta(days=1)
        )

    if expected_date <= date_to:
        raise PayrollPeriodLifecycleError(
            "Payroll input salary-rate snapshot does not cover "
            "the full contract-period intersection"
        )

    if slices[-1].effective_to != date_to:
        raise PayrollPeriodLifecycleError(
            "Payroll input salary-rate snapshot exceeds "
            "the contract-period intersection"
        )

    if payroll_input.scheduled_minutes < 0:
        raise PayrollPeriodLifecycleError(
            "Payroll input scheduled_minutes is invalid"
        )

    if payroll_input.worked_minutes < 0:
        raise PayrollPeriodLifecycleError(
            "Payroll input worked_minutes is invalid"
        )

    if payroll_input.leave_days < 0:
        raise PayrollPeriodLifecycleError(
            "Payroll input leave_days is invalid"
        )

    if payroll_input.sick_days < 0:
        raise PayrollPeriodLifecycleError(
            "Payroll input sick_days is invalid"
        )

    if (
        payroll_input.manual_adjustment_amount != 0
        and (
            payroll_input.manual_adjustment_reason is None
            or not payroll_input.manual_adjustment_reason.strip()
        )
    ):
        raise PayrollPeriodLifecycleError(
            "Non-zero manual adjustment requires a reason"
        )


async def _validate_period_for_finalization(
    db: AsyncSession,
    *,
    payroll_period: PayrollPeriod,
) -> None:
    from app.models.payroll import PayrollInput

    result = await db.execute(
        select(PayrollInput)
        .where(
            PayrollInput.company_id
            == payroll_period.company_id,
            PayrollInput.payroll_period_id
            == payroll_period.id,
        )
        .order_by(PayrollInput.id)
    )

    payroll_inputs = list(result.scalars().all())

    if not payroll_inputs:
        raise PayrollPeriodLifecycleError(
            "Payroll period cannot be finalized without payroll inputs"
        )

    from app.services.payroll_input_service import refresh_payroll_input, PayrollInputError

    for payroll_input in payroll_inputs:
        try:
            await refresh_payroll_input(db, company_id=payroll_period.company_id,
                                       payroll_input_id=payroll_input.id)
        except PayrollInputError as exc:
            raise PayrollPeriodLifecycleError(str(exc)) from exc
        await _validate_payroll_input_for_finalization(
            db,
            payroll_period=payroll_period,
            payroll_input=payroll_input,
        )


@serialized_payroll_mutation(PayrollPeriodLifecycleError)
async def finalize_payroll_period(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
    finalized_by: int,
) -> PayrollPeriod:
    payroll_period = await _require_payroll_period(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        lock=True,
    )

    if payroll_period.status != PayrollPeriodStatus.DRAFT:
        raise PayrollPeriodLifecycleError(
            "Only draft payroll periods can be finalized"
        )

    await _validate_period_for_finalization(
        db,
        payroll_period=payroll_period,
    )

    payroll_period.status = PayrollPeriodStatus.FINALIZED
    payroll_period.finalized_by = finalized_by
    payroll_period.finalized_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(payroll_period)

    return payroll_period
