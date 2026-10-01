from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.hr_change import HRChangeResponse
from app.schemas.time_attendance import (
    AttendanceCreate,
    AttendanceRead,
    AttendanceUpdate,
    EmploymentScheduleAssignmentCreate,
    EmploymentScheduleAssignmentRead,
    WorkScheduleCreate,
    WorkScheduleDayRead,
    WorkScheduleRead,
    WorkScheduleUpdate,
)
from app.services.hr_change_service import list_changes
from app.services.time_attendance_service import (
    TimeAttendanceConflictError,
    TimeAttendanceError,
    TimeAttendanceLifecycleError,
    TimeAttendanceNotFoundError,
    create_attendance,
    create_schedule_assignment,
    create_work_schedule,
    get_attendance,
    get_work_schedule,
    list_attendance,
    list_schedule_assignments,
    list_work_schedule_days,
    list_work_schedules,
    update_attendance,
    update_work_schedule,
)


router = APIRouter(
    prefix="/companies/{company_id}",
    tags=["time-attendance"],
)


def _http_error(
    exc: TimeAttendanceError,
) -> HTTPException:
    if isinstance(exc, TimeAttendanceNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(exc, TimeAttendanceConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if isinstance(exc, TimeAttendanceLifecycleError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    )


async def _commit_write(
    db: AsyncSession,
) -> None:
    await db.commit()


@router.get(
    "/work-schedules",
    response_model=list[WorkScheduleRead],
)
async def get_work_schedules(
    company_id: int,
    active_only: bool = Query(default=False),
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await list_work_schedules(
            db,
            company_id=company_id,
            active_only=active_only,
        )
    except TimeAttendanceError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/work-schedules",
    response_model=WorkScheduleRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_work_schedule(
    company_id: int,
    data: WorkScheduleCreate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await create_work_schedule(
            db,
            company_id=company_id,
            created_by=current_user.id,
            data=data,
        )
        await _commit_write(db)
        await db.refresh(obj)
        return obj
    except TimeAttendanceError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Work schedule data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/work-schedules/{schedule_id}",
    response_model=WorkScheduleRead,
)
async def get_one_work_schedule(
    company_id: int,
    schedule_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_work_schedule(
            db,
            company_id=company_id,
            schedule_id=schedule_id,
        )
    except TimeAttendanceError as exc:
        raise _http_error(exc) from exc


@router.patch(
    "/work-schedules/{schedule_id}",
    response_model=WorkScheduleRead,
)
async def patch_work_schedule(
    company_id: int,
    schedule_id: int,
    data: WorkScheduleUpdate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await update_work_schedule(
            db,
            company_id=company_id,
            schedule_id=schedule_id,
            data=data,
            changed_by=current_user.id,
        )
        await _commit_write(db)
        await db.refresh(obj)
        return obj
    except TimeAttendanceError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Work schedule data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/work-schedules/{schedule_id}/days",
    response_model=list[WorkScheduleDayRead],
    include_in_schema=False,
)
async def get_work_schedule_days(
    company_id: int,
    schedule_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await list_work_schedule_days(
            db,
            company_id=company_id,
            schedule_id=schedule_id,
        )
    except TimeAttendanceError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/employment-contracts/{contract_id}/schedule-assignments",
    response_model=list[EmploymentScheduleAssignmentRead],
)
async def get_schedule_assignments(
    company_id: int,
    contract_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await list_schedule_assignments(
            db,
            company_id=company_id,
            contract_id=contract_id,
        )
    except TimeAttendanceError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/employment-contracts/{contract_id}/schedule-assignments",
    response_model=EmploymentScheduleAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_schedule_assignment(
    company_id: int,
    contract_id: int,
    data: EmploymentScheduleAssignmentCreate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await create_schedule_assignment(
            db,
            company_id=company_id,
            contract_id=contract_id,
            created_by=current_user.id,
            data=data,
        )
        await _commit_write(db)
        await db.refresh(obj)
        return obj
    except TimeAttendanceError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Schedule assignment data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/attendance",
    response_model=list[AttendanceRead],
)
async def get_attendance_records(
    company_id: int,
    contract_id: int | None = Query(default=None, gt=0),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    if (
        date_from is not None
        and date_to is not None
        and date_to < date_from
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_to cannot be before date_from",
        )

    try:
        return await list_attendance(
            db,
            company_id=company_id,
            contract_id=contract_id,
            date_from=date_from,
            date_to=date_to,
        )
    except TimeAttendanceError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/attendance",
    response_model=AttendanceRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_attendance(
    company_id: int,
    data: AttendanceCreate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await create_attendance(
            db,
            company_id=company_id,
            created_by=current_user.id,
            data=data,
        )
        await _commit_write(db)
        await db.refresh(obj)
        return obj
    except TimeAttendanceError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Attendance data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/attendance/{attendance_id}",
    response_model=AttendanceRead,
)
async def get_one_attendance(
    company_id: int,
    attendance_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_attendance(
            db,
            company_id=company_id,
            attendance_id=attendance_id,
        )
    except TimeAttendanceError as exc:
        raise _http_error(exc) from exc


@router.patch(
    "/attendance/{attendance_id}",
    response_model=AttendanceRead,
)
async def patch_attendance(
    company_id: int,
    attendance_id: int,
    data: AttendanceUpdate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await update_attendance(
            db,
            company_id=company_id,
            attendance_id=attendance_id,
            data=data,
            changed_by=current_user.id,
        )
        await _commit_write(db)
        await db.refresh(obj)
        return obj
    except TimeAttendanceError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Attendance data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/attendance/{attendance_id}/history",
    response_model=list[HRChangeResponse],
)
async def get_attendance_history(
    company_id: int,
    attendance_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        await get_attendance(
            db,
            company_id=company_id,
            attendance_id=attendance_id,
        )
    except TimeAttendanceError as exc:
        raise _http_error(exc) from exc

    return await list_changes(
        db,
        company_id,
        "attendance_record",
        attendance_id,
    )
