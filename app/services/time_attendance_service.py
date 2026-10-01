from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employment_contract import (
    EmploymentContract,
    EmploymentContractStatus,
)
from app.models.time_attendance import (
    AttendanceRecord,
    AttendanceStatus,
    EmploymentScheduleAssignment,
    WorkSchedule,
    WorkScheduleDay,
)
from app.services.hr_change_service import record_change, snapshot
from app.schemas.time_attendance import (
    AttendanceCreate,
    AttendanceUpdate,
    EmploymentScheduleAssignmentCreate,
    WorkScheduleCreate,
    WorkScheduleDayInput,
    WorkScheduleUpdate,
)


class TimeAttendanceError(ValueError):
    pass


class TimeAttendanceNotFoundError(TimeAttendanceError):
    pass


class TimeAttendanceLifecycleError(TimeAttendanceError):
    pass


class TimeAttendanceConflictError(TimeAttendanceError):
    pass


async def _require_contract(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int,
) -> EmploymentContract:
    row = await db.scalar(
        select(EmploymentContract).where(
            EmploymentContract.company_id == company_id,
            EmploymentContract.id == contract_id,
        )
    )

    if row is None:
        raise TimeAttendanceNotFoundError(
            "employment contract not found"
        )

    return row


async def _require_schedule(
    db: AsyncSession,
    *,
    company_id: int,
    schedule_id: int,
) -> WorkSchedule:
    row = await db.scalar(
        select(WorkSchedule).where(
            WorkSchedule.company_id == company_id,
            WorkSchedule.id == schedule_id,
        )
    )

    if row is None:
        raise TimeAttendanceNotFoundError(
            "work schedule not found"
        )

    return row


def _validate_contract_date(
    contract: EmploymentContract,
    *,
    value: date,
    operation: str,
) -> None:
    if contract.status == EmploymentContractStatus.CANCELLED:
        raise TimeAttendanceLifecycleError(
            f"cancelled employment contract cannot receive {operation}"
        )

    if value < contract.start_date:
        raise TimeAttendanceLifecycleError(
            f"{operation} date cannot be before employment "
            "contract start_date"
        )

    if (
        contract.end_date is not None
        and value > contract.end_date
    ):
        raise TimeAttendanceLifecycleError(
            f"{operation} date cannot be after employment "
            "contract end_date"
        )


def _validate_assignment_range(
    contract: EmploymentContract,
    *,
    effective_from: date,
    effective_to: date | None,
) -> None:
    _validate_contract_date(
        contract,
        value=effective_from,
        operation="schedule assignment",
    )

    if (
        effective_to is not None
        and effective_to < effective_from
    ):
        raise TimeAttendanceLifecycleError(
            "effective_to cannot be before effective_from"
        )

    if (
        effective_to is not None
        and effective_to < contract.start_date
    ):
        raise TimeAttendanceLifecycleError(
            "schedule assignment cannot end before employment "
            "contract start_date"
        )

    if (
        contract.end_date is not None
        and effective_to is not None
        and effective_to > contract.end_date
    ):
        raise TimeAttendanceLifecycleError(
            "schedule assignment cannot end after employment "
            "contract end_date"
        )

    if (
        contract.end_date is not None
        and effective_to is None
    ):
        raise TimeAttendanceLifecycleError(
            "open-ended schedule assignment cannot exceed a "
            "closed employment contract"
        )


async def _ensure_schedule_code_unique(
    db: AsyncSession,
    *,
    company_id: int,
    code: str,
    exclude_id: int | None = None,
) -> None:
    query = select(WorkSchedule.id).where(
        WorkSchedule.company_id == company_id,
        WorkSchedule.code == code,
    )

    if exclude_id is not None:
        query = query.where(
            WorkSchedule.id != exclude_id
        )

    if await db.scalar(query.limit(1)) is not None:
        raise TimeAttendanceConflictError(
            "work schedule code already exists"
        )


async def _replace_schedule_days(
    db: AsyncSession,
    *,
    company_id: int,
    schedule_id: int,
    days: list[WorkScheduleDayInput],
) -> None:
    await db.execute(
        delete(WorkScheduleDay).where(
            WorkScheduleDay.company_id == company_id,
            WorkScheduleDay.work_schedule_id == schedule_id,
        )
    )

    for item in days:
        db.add(
            WorkScheduleDay(
                company_id=company_id,
                work_schedule_id=schedule_id,
                weekday=item.weekday,
                is_working_day=item.is_working_day,
                start_time=item.start_time,
                end_time=item.end_time,
                break_minutes=item.break_minutes,
            )
        )


async def list_work_schedules(
    db: AsyncSession,
    *,
    company_id: int,
    active_only: bool = False,
) -> list[WorkSchedule]:
    query = select(WorkSchedule).where(
        WorkSchedule.company_id == company_id
    )

    if active_only:
        query = query.where(
            WorkSchedule.is_active.is_(True)
        )

    query = query.order_by(
        WorkSchedule.code,
        WorkSchedule.id,
    )

    return list((await db.scalars(query)).all())


async def get_work_schedule(
    db: AsyncSession,
    *,
    company_id: int,
    schedule_id: int,
) -> WorkSchedule:
    return await _require_schedule(
        db,
        company_id=company_id,
        schedule_id=schedule_id,
    )


async def list_work_schedule_days(
    db: AsyncSession,
    *,
    company_id: int,
    schedule_id: int,
) -> list[WorkScheduleDay]:
    await _require_schedule(
        db,
        company_id=company_id,
        schedule_id=schedule_id,
    )

    query = (
        select(WorkScheduleDay)
        .where(
            WorkScheduleDay.company_id == company_id,
            WorkScheduleDay.work_schedule_id == schedule_id,
        )
        .order_by(
            WorkScheduleDay.weekday,
            WorkScheduleDay.id,
        )
    )

    return list((await db.scalars(query)).all())


async def create_work_schedule(
    db: AsyncSession,
    *,
    company_id: int,
    created_by: int,
    data: WorkScheduleCreate,
) -> WorkSchedule:
    await _ensure_schedule_code_unique(
        db,
        company_id=company_id,
        code=data.code,
    )

    ZoneInfo(data.timezone)

    row = WorkSchedule(
        company_id=company_id,
        code=data.code,
        name=data.name,
        timezone=data.timezone,
        is_active=True,
        created_by=created_by,
    )

    db.add(row)
    await db.flush()

    await _replace_schedule_days(
        db,
        company_id=company_id,
        schedule_id=row.id,
        days=data.days,
    )

    await db.flush()
    await db.refresh(row)

    await record_change(
        db,
        row,
        "work_schedule",
        None,
        created_by,
    )

    await db.flush()

    return row


async def update_work_schedule(
    db: AsyncSession,
    *,
    company_id: int,
    schedule_id: int,
    data: WorkScheduleUpdate,
    changed_by: int | None = None,
) -> WorkSchedule:
    row = await _require_schedule(
        db,
        company_id=company_id,
        schedule_id=schedule_id,
    )


    before = snapshot(row)
    supplied = data.model_fields_set

    if "code" in supplied:
        if data.code is None:
            raise TimeAttendanceError(
                "code cannot be null"
            )
        await _ensure_schedule_code_unique(
            db,
            company_id=company_id,
            code=data.code,
            exclude_id=row.id,
        )
        row.code = data.code

    if "name" in supplied:
        if data.name is None:
            raise TimeAttendanceError(
                "name cannot be null"
            )
        row.name = data.name

    if "timezone" in supplied:
        if data.timezone is None:
            raise TimeAttendanceError(
                "timezone cannot be null"
            )
        ZoneInfo(data.timezone)
        row.timezone = data.timezone

    if "is_active" in supplied:
        if data.is_active is None:
            raise TimeAttendanceError(
                "is_active cannot be null"
            )
        row.is_active = data.is_active

    if "days" in supplied:
        if data.days is None:
            raise TimeAttendanceError(
                "days cannot be null"
            )

        await _replace_schedule_days(
            db,
            company_id=company_id,
            schedule_id=row.id,
            days=data.days,
        )

    await db.flush()
    await db.refresh(row)

    await record_change(
        db,
        row,
        "work_schedule",
        before,
        changed_by,
    )

    await db.flush()

    return row


async def list_schedule_assignments(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int,
) -> list[EmploymentScheduleAssignment]:
    await _require_contract(
        db,
        company_id=company_id,
        contract_id=contract_id,
    )

    query = (
        select(EmploymentScheduleAssignment)
        .where(
            EmploymentScheduleAssignment.company_id == company_id,
            EmploymentScheduleAssignment.employment_contract_id
            == contract_id,
        )
        .order_by(
            EmploymentScheduleAssignment.effective_from,
            EmploymentScheduleAssignment.id,
        )
    )

    return list((await db.scalars(query)).all())


async def create_schedule_assignment(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int,
    created_by: int,
    data: EmploymentScheduleAssignmentCreate,
) -> EmploymentScheduleAssignment:
    contract = await _require_contract(
        db,
        company_id=company_id,
        contract_id=contract_id,
    )

    schedule = await _require_schedule(
        db,
        company_id=company_id,
        schedule_id=data.work_schedule_id,
    )

    if not schedule.is_active:
        raise TimeAttendanceLifecycleError(
            "inactive work schedule cannot receive new assignments"
        )

    _validate_assignment_range(
        contract,
        effective_from=data.effective_from,
        effective_to=data.effective_to,
    )

    overlap = await db.scalar(
        select(EmploymentScheduleAssignment.id)
        .where(
            EmploymentScheduleAssignment.company_id == company_id,
            EmploymentScheduleAssignment.employment_contract_id
            == contract_id,
            or_(
                EmploymentScheduleAssignment.effective_to.is_(None),
                EmploymentScheduleAssignment.effective_to
                >= data.effective_from,
            ),
            or_(
                data.effective_to is None,
                EmploymentScheduleAssignment.effective_from
                <= data.effective_to,
            ),
        )
        .limit(1)
    )

    if overlap is not None:
        raise TimeAttendanceConflictError(
            "schedule assignment overlaps an existing assignment"
        )

    row = EmploymentScheduleAssignment(
        company_id=company_id,
        employment_contract_id=contract_id,
        work_schedule_id=schedule.id,
        effective_from=data.effective_from,
        effective_to=data.effective_to,
        created_by=created_by,
    )

    db.add(row)
    await db.flush()
    await db.refresh(row)

    await record_change(
        db,
        row,
        "employment_schedule_assignment",
        None,
        created_by,
    )

    await db.flush()

    return row


async def _schedule_for_work_date(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int,
    work_date: date,
) -> WorkSchedule | None:
    query = (
        select(WorkSchedule)
        .join(
            EmploymentScheduleAssignment,
            and_(
                EmploymentScheduleAssignment.company_id
                == WorkSchedule.company_id,
                EmploymentScheduleAssignment.work_schedule_id
                == WorkSchedule.id,
            ),
        )
        .where(
            EmploymentScheduleAssignment.company_id == company_id,
            EmploymentScheduleAssignment.employment_contract_id
            == contract_id,
            EmploymentScheduleAssignment.effective_from
            <= work_date,
            or_(
                EmploymentScheduleAssignment.effective_to.is_(None),
                EmploymentScheduleAssignment.effective_to
                >= work_date,
            ),
        )
        .order_by(
            EmploymentScheduleAssignment.effective_from.desc(),
            EmploymentScheduleAssignment.id.desc(),
        )
    )

    return await db.scalar(query.limit(1))


def _worked_minutes(
    *,
    status: AttendanceStatus,
    actual_start_at: datetime | None,
    actual_end_at: datetime | None,
    break_minutes: int,
) -> int:
    if status != AttendanceStatus.PRESENT:
        if actual_start_at is not None or actual_end_at is not None:
            raise TimeAttendanceError(
                "non-present attendance cannot define worked timestamps"
            )
        if break_minutes != 0:
            raise TimeAttendanceError(
                "non-present attendance must have zero break_minutes"
            )
        return 0

    if actual_start_at is None or actual_end_at is None:
        raise TimeAttendanceError(
            "present attendance requires actual_start_at "
            "and actual_end_at"
        )

    if (
        actual_start_at.utcoffset() is None
        or actual_end_at.utcoffset() is None
    ):
        raise TimeAttendanceError(
            "attendance timestamps must be timezone-aware"
        )

    if actual_end_at <= actual_start_at:
        raise TimeAttendanceError(
            "actual_end_at must be after actual_start_at"
        )

    gross_minutes = int(
        (actual_end_at - actual_start_at).total_seconds() // 60
    )

    if break_minutes < 0:
        raise TimeAttendanceError(
            "break_minutes cannot be negative"
        )

    if break_minutes >= gross_minutes:
        raise TimeAttendanceError(
            "break_minutes must leave positive worked time"
        )

    return gross_minutes - break_minutes


def _validate_local_work_date(
    *,
    schedule: WorkSchedule | None,
    work_date: date,
    actual_start_at: datetime | None,
) -> None:
    if schedule is None or actual_start_at is None:
        return

    local_date = actual_start_at.astimezone(
        ZoneInfo(schedule.timezone)
    ).date()

    if local_date != work_date:
        raise TimeAttendanceError(
            "work_date must equal the local schedule date of "
            "actual_start_at"
        )


async def _ensure_attendance_unique(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int,
    work_date: date,
    exclude_id: int | None = None,
) -> None:
    query = select(AttendanceRecord.id).where(
        AttendanceRecord.company_id == company_id,
        AttendanceRecord.employment_contract_id == contract_id,
        AttendanceRecord.work_date == work_date,
    )

    if exclude_id is not None:
        query = query.where(
            AttendanceRecord.id != exclude_id
        )

    if await db.scalar(query.limit(1)) is not None:
        raise TimeAttendanceConflictError(
            "attendance already exists for contract and work_date"
        )


async def list_attendance(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[AttendanceRecord]:
    query = select(AttendanceRecord).where(
        AttendanceRecord.company_id == company_id
    )

    if contract_id is not None:
        await _require_contract(
            db,
            company_id=company_id,
            contract_id=contract_id,
        )
        query = query.where(
            AttendanceRecord.employment_contract_id
            == contract_id
        )

    if date_from is not None:
        query = query.where(
            AttendanceRecord.work_date >= date_from
        )

    if date_to is not None:
        query = query.where(
            AttendanceRecord.work_date <= date_to
        )

    if (
        date_from is not None
        and date_to is not None
        and date_to < date_from
    ):
        raise TimeAttendanceError(
            "date_to cannot be before date_from"
        )

    query = query.order_by(
        AttendanceRecord.work_date.desc(),
        AttendanceRecord.id.desc(),
    )

    return list((await db.scalars(query)).all())


async def get_attendance(
    db: AsyncSession,
    *,
    company_id: int,
    attendance_id: int,
) -> AttendanceRecord:
    row = await db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.company_id == company_id,
            AttendanceRecord.id == attendance_id,
        )
    )

    if row is None:
        raise TimeAttendanceNotFoundError(
            "attendance record not found"
        )

    return row


async def create_attendance(
    db: AsyncSession,
    *,
    company_id: int,
    created_by: int,
    data: AttendanceCreate,
) -> AttendanceRecord:
    contract = await _require_contract(
        db,
        company_id=company_id,
        contract_id=data.employment_contract_id,
    )

    _validate_contract_date(
        contract,
        value=data.work_date,
        operation="attendance",
    )

    await _ensure_attendance_unique(
        db,
        company_id=company_id,
        contract_id=contract.id,
        work_date=data.work_date,
    )

    schedule = await _schedule_for_work_date(
        db,
        company_id=company_id,
        contract_id=contract.id,
        work_date=data.work_date,
    )

    _validate_local_work_date(
        schedule=schedule,
        work_date=data.work_date,
        actual_start_at=data.actual_start_at,
    )

    worked_minutes = _worked_minutes(
        status=data.status,
        actual_start_at=data.actual_start_at,
        actual_end_at=data.actual_end_at,
        break_minutes=data.break_minutes,
    )

    row = AttendanceRecord(
        company_id=company_id,
        employment_contract_id=contract.id,
        work_date=data.work_date,
        status=data.status,
        actual_start_at=data.actual_start_at,
        actual_end_at=data.actual_end_at,
        break_minutes=data.break_minutes,
        worked_minutes=worked_minutes,
        source=data.source,
        note=data.note,
        created_by=created_by,
    )

    db.add(row)
    await db.flush()
    await db.refresh(row)

    await record_change(
        db,
        row,
        "attendance_record",
        None,
        created_by,
    )

    await db.flush()

    return row


async def update_attendance(
    db: AsyncSession,
    *,
    company_id: int,
    attendance_id: int,
    data: AttendanceUpdate,
    changed_by: int | None = None,
) -> AttendanceRecord:
    row = await get_attendance(
        db,
        company_id=company_id,
        attendance_id=attendance_id,
    )


    before = snapshot(row)
    contract = await _require_contract(
        db,
        company_id=company_id,
        contract_id=row.employment_contract_id,
    )

    _validate_contract_date(
        contract,
        value=row.work_date,
        operation="attendance",
    )

    supplied = data.model_fields_set

    next_status = (
        data.status
        if "status" in supplied
        else row.status
    )
    next_start = (
        data.actual_start_at
        if "actual_start_at" in supplied
        else row.actual_start_at
    )
    next_end = (
        data.actual_end_at
        if "actual_end_at" in supplied
        else row.actual_end_at
    )
    next_break = (
        data.break_minutes
        if "break_minutes" in supplied
        else row.break_minutes
    )

    if next_status is None:
        raise TimeAttendanceError(
            "status cannot be null"
        )

    if next_break is None:
        raise TimeAttendanceError(
            "break_minutes cannot be null"
        )

    if next_status != AttendanceStatus.PRESENT:
        next_start = None
        next_end = None
        next_break = 0

    schedule = await _schedule_for_work_date(
        db,
        company_id=company_id,
        contract_id=row.employment_contract_id,
        work_date=row.work_date,
    )

    _validate_local_work_date(
        schedule=schedule,
        work_date=row.work_date,
        actual_start_at=next_start,
    )

    worked_minutes = _worked_minutes(
        status=next_status,
        actual_start_at=next_start,
        actual_end_at=next_end,
        break_minutes=next_break,
    )

    row.status = next_status
    row.actual_start_at = next_start
    row.actual_end_at = next_end
    row.break_minutes = next_break
    row.worked_minutes = worked_minutes

    if "source" in supplied:
        if data.source is None:
            raise TimeAttendanceError(
                "source cannot be null"
            )
        row.source = data.source

    if "note" in supplied:
        row.note = data.note

    await db.flush()
    await db.refresh(row)

    await record_change(
        db,
        row,
        "attendance_record",
        before,
        changed_by,
    )

    await db.flush()

    return row
