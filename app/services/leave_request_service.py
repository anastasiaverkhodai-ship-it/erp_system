from datetime import date, datetime, timedelta, timezone

from app.services.payroll_mutation_guard import serialized_payroll_mutation, ensure_payroll_source_editable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employment_contract import EmploymentContract
from app.models.leave_request import (
    LeaveRequest,
    LeaveRequestStatus,
)
from app.models.time_attendance import (
    AttendanceRecord,
    AttendanceSource,
    AttendanceStatus,
)
from app.schemas.leave_request import LeaveRequestCreate
from app.services.hr_change_service import record_change, snapshot


def _attendance_status_for_leave(
    leave_request: LeaveRequest,
) -> AttendanceStatus:
    leave_type = _enum_value(
        leave_request.leave_type
    )

    if leave_type == "sick":
        return AttendanceStatus.SICK

    return AttendanceStatus.LEAVE


def _iter_leave_dates(
    start_date: date,
    end_date: date,
):
    current = start_date

    while current <= end_date:
        yield current
        current += timedelta(days=1)


async def _sync_leave_attendance(
    db: AsyncSession,
    *,
    leave_request: LeaveRequest,
) -> None:
    attendance_status = (
        _attendance_status_for_leave(
            leave_request
        )
    )

    for work_date in _iter_leave_dates(
        leave_request.start_date,
        leave_request.end_date,
    ):
        result = await db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.company_id
                == leave_request.company_id,
                AttendanceRecord.employment_contract_id
                == leave_request.employment_contract_id,
                AttendanceRecord.work_date
                == work_date,
            )
        )

        attendance = result.scalar_one_or_none()

        if attendance is None:
            attendance = AttendanceRecord(
                company_id=leave_request.company_id,
                employment_contract_id=(
                    leave_request.employment_contract_id
                ),
                leave_request_id=leave_request.id,
                work_date=work_date,
                status=attendance_status,
                actual_start_at=None,
                actual_end_at=None,
                break_minutes=0,
                worked_minutes=0,
                source=AttendanceSource.SYSTEM,
                note=None,
                created_by=leave_request.approved_by,
            )

            db.add(attendance)
            continue

        same_source = (
            attendance.leave_request_id
            == leave_request.id
            and attendance.source
            == AttendanceSource.SYSTEM
        )

        if not same_source:
            raise LeaveRequestConflictError(
                "Attendance already exists for leave date"
            )

        attendance.status = attendance_status
        attendance.actual_start_at = None
        attendance.actual_end_at = None
        attendance.break_minutes = 0
        attendance.worked_minutes = 0

    await db.flush()


async def _reverse_leave_attendance(
    db: AsyncSession,
    *,
    leave_request: LeaveRequest,
) -> None:
    result = await db.execute(
        select(AttendanceRecord).where(
            AttendanceRecord.company_id
            == leave_request.company_id,
            AttendanceRecord.leave_request_id
            == leave_request.id,
        )
    )

    records = list(result.scalars().all())

    for attendance in records:
        if (
            attendance.source
            != AttendanceSource.SYSTEM
        ):
            raise LeaveRequestConflictError(
                "Leave-linked attendance is not system generated"
            )

        await db.delete(attendance)

    await db.flush()


class LeaveRequestError(Exception):
    pass


class LeaveRequestNotFoundError(LeaveRequestError):
    pass


class LeaveRequestLifecycleError(LeaveRequestError):
    pass


class LeaveRequestConflictError(LeaveRequestError):
    pass


def _enum_value(value) -> str:
    return getattr(value, "value", value)


async def _require_contract(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int,
) -> EmploymentContract:
    result = await db.execute(
        select(EmploymentContract).where(
            EmploymentContract.company_id == company_id,
            EmploymentContract.id == contract_id,
        )
    )

    contract = result.scalar_one_or_none()

    if contract is None:
        raise LeaveRequestNotFoundError(
            "Employment contract not found"
        )

    return contract


async def _require_leave_request(
    db: AsyncSession,
    *,
    company_id: int,
    leave_request_id: int,
) -> LeaveRequest:
    result = await db.execute(
        select(LeaveRequest).execution_options(populate_existing=True).where(
            LeaveRequest.company_id == company_id,
            LeaveRequest.id == leave_request_id,
        )
    )

    leave_request = result.scalar_one_or_none()

    if leave_request is None:
        raise LeaveRequestNotFoundError(
            "Leave request not found"
        )

    return leave_request


def _validate_contract_range(
    contract: EmploymentContract,
    *,
    start_date: date,
    end_date: date,
) -> None:
    if end_date < start_date:
        raise LeaveRequestLifecycleError(
            "Leave end_date cannot precede start_date"
        )

    if start_date < contract.start_date:
        raise LeaveRequestLifecycleError(
            "Leave cannot start before employment contract"
        )

    if (
        contract.end_date is not None
        and end_date > contract.end_date
    ):
        raise LeaveRequestLifecycleError(
            "Leave cannot extend beyond employment contract"
        )

    status = _enum_value(contract.status).lower()

    if status == "cancelled":
        raise LeaveRequestLifecycleError(
            "Cancelled employment contract cannot receive leave requests"
        )


async def _ensure_no_active_overlap(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int,
    start_date: date,
    end_date: date,
    exclude_id: int | None = None,
) -> None:
    conditions = [
        LeaveRequest.company_id == company_id,
        LeaveRequest.employment_contract_id == contract_id,
        LeaveRequest.status.in_(
            [
                LeaveRequestStatus.PENDING,
                LeaveRequestStatus.APPROVED,
            ]
        ),
        LeaveRequest.start_date <= end_date,
        LeaveRequest.end_date >= start_date,
    ]

    if exclude_id is not None:
        conditions.append(
            LeaveRequest.id != exclude_id
        )

    result = await db.execute(
        select(LeaveRequest.id)
        .where(and_(*conditions))
        .limit(1)
    )

    if result.scalar_one_or_none() is not None:
        raise LeaveRequestConflictError(
            "Leave request overlaps an existing active leave request"
        )


async def list_leave_requests(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int | None = None,
    status: LeaveRequestStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[LeaveRequest]:
    conditions = [
        LeaveRequest.company_id == company_id,
    ]

    if contract_id is not None:
        conditions.append(
            LeaveRequest.employment_contract_id
            == contract_id
        )

    if status is not None:
        conditions.append(
            LeaveRequest.status == status
        )

    if date_from is not None:
        conditions.append(
            LeaveRequest.end_date >= date_from
        )

    if date_to is not None:
        conditions.append(
            LeaveRequest.start_date <= date_to
        )

    result = await db.execute(
        select(LeaveRequest)
        .where(and_(*conditions))
        .order_by(
            LeaveRequest.start_date.desc(),
            LeaveRequest.id.desc(),
        )
    )

    return list(result.scalars().all())


async def get_leave_request(
    db: AsyncSession,
    *,
    company_id: int,
    leave_request_id: int,
) -> LeaveRequest:
    return await _require_leave_request(
        db,
        company_id=company_id,
        leave_request_id=leave_request_id,
    )


@serialized_payroll_mutation(LeaveRequestLifecycleError)
async def create_leave_request(
    db: AsyncSession,
    *,
    company_id: int,
    requested_by: int,
    data: LeaveRequestCreate,
) -> LeaveRequest:
    contract = await _require_contract(
        db,
        company_id=company_id,
        contract_id=data.employment_contract_id,
    )

    _validate_contract_range(
        contract,
        start_date=data.start_date,
        end_date=data.end_date,
    )

    await _ensure_no_active_overlap(
        db,
        company_id=company_id,
        contract_id=data.employment_contract_id,
        start_date=data.start_date,
        end_date=data.end_date,
    )

    leave_request = LeaveRequest(
        company_id=company_id,
        employment_contract_id=data.employment_contract_id,
        leave_type=data.leave_type,
        start_date=data.start_date,
        end_date=data.end_date,
        status=LeaveRequestStatus.PENDING,
        reason=data.reason,
        requested_by=requested_by,
    )

    db.add(leave_request)
    await db.flush()
    await db.refresh(leave_request)

    await record_change(
        db,
        leave_request,
        "leave_request",
        None,
        requested_by,
    )

    await db.flush()

    return leave_request


@serialized_payroll_mutation(LeaveRequestLifecycleError)
async def approve_leave_request(
    db: AsyncSession,
    *,
    company_id: int,
    leave_request_id: int,
    approved_by: int,
) -> LeaveRequest:
    leave_request = await _require_leave_request(
        db,
        company_id=company_id,
        leave_request_id=leave_request_id,
    )

    await ensure_payroll_source_editable(db, company_id=company_id,
        contract_id=leave_request.employment_contract_id,
        date_from=leave_request.start_date, date_to=leave_request.end_date,
        error_type=LeaveRequestLifecycleError)

    if leave_request.status != LeaveRequestStatus.PENDING:
        raise LeaveRequestLifecycleError(
            "Only pending leave requests can be approved"
        )

    contract = await _require_contract(
        db,
        company_id=company_id,
        contract_id=leave_request.employment_contract_id,
    )

    _validate_contract_range(
        contract,
        start_date=leave_request.start_date,
        end_date=leave_request.end_date,
    )

    before = snapshot(leave_request)

    leave_request.status = LeaveRequestStatus.APPROVED
    leave_request.approved_by = approved_by
    leave_request.approved_at = datetime.now(timezone.utc)

    await db.flush()

    await _sync_leave_attendance(
        db,
        leave_request=leave_request,
    )

    await db.refresh(leave_request)

    await record_change(
        db,
        leave_request,
        "leave_request",
        before,
        approved_by,
    )

    await db.flush()

    return leave_request


@serialized_payroll_mutation(LeaveRequestLifecycleError)
async def reject_leave_request(
    db: AsyncSession,
    *,
    company_id: int,
    leave_request_id: int,
    rejected_by: int,
    reason: str | None = None,
) -> LeaveRequest:
    leave_request = await _require_leave_request(
        db,
        company_id=company_id,
        leave_request_id=leave_request_id,
    )

    if leave_request.status != LeaveRequestStatus.PENDING:
        raise LeaveRequestLifecycleError(
            "Only pending leave requests can be rejected"
        )

    before = snapshot(leave_request)

    if reason is not None:
        leave_request.reason = reason

    leave_request.status = LeaveRequestStatus.REJECTED
    leave_request.rejected_by = rejected_by
    leave_request.rejected_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(leave_request)

    await record_change(
        db,
        leave_request,
        "leave_request",
        before,
        rejected_by,
    )

    await db.flush()

    return leave_request


@serialized_payroll_mutation(LeaveRequestLifecycleError)
async def cancel_leave_request(
    db: AsyncSession,
    *,
    company_id: int,
    leave_request_id: int,
    cancelled_by: int,
    reason: str | None = None,
) -> LeaveRequest:
    leave_request = await _require_leave_request(
        db,
        company_id=company_id,
        leave_request_id=leave_request_id,
    )

    await ensure_payroll_source_editable(db, company_id=company_id,
        contract_id=leave_request.employment_contract_id,
        date_from=leave_request.start_date, date_to=leave_request.end_date,
        error_type=LeaveRequestLifecycleError)

    if leave_request.status not in {
        LeaveRequestStatus.PENDING,
        LeaveRequestStatus.APPROVED,
    }:
        raise LeaveRequestLifecycleError(
            "Only pending or approved leave requests can be cancelled"
        )

    before = snapshot(leave_request)

    was_approved = (
        leave_request.status
        == LeaveRequestStatus.APPROVED
    )

    if reason is not None:
        leave_request.reason = reason

    leave_request.status = LeaveRequestStatus.CANCELLED
    leave_request.cancelled_by = cancelled_by
    leave_request.cancelled_at = datetime.now(timezone.utc)

    if was_approved:
        await _reverse_leave_attendance(
            db,
            leave_request=leave_request,
        )

    await db.flush()
    await db.refresh(leave_request)

    await record_change(
        db,
        leave_request,
        "leave_request",
        before,
        cancelled_by,
    )

    await db.flush()

    return leave_request
