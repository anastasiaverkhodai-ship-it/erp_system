from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.hr_change import HRChange
from app.models.leave_request import LeaveRequestStatus
from app.schemas.leave_request import (
    LeaveRequestCancel,
    LeaveRequestCreate,
    LeaveRequestRead,
    LeaveRequestReject,
)
from app.services.leave_request_service import (
    LeaveRequestConflictError,
    LeaveRequestError,
    LeaveRequestLifecycleError,
    LeaveRequestNotFoundError,
    approve_leave_request,
    cancel_leave_request,
    create_leave_request,
    get_leave_request,
    list_leave_requests,
    reject_leave_request,
)

router = APIRouter()


def _http_error(exc: LeaveRequestError) -> HTTPException:
    if isinstance(exc, LeaveRequestNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(exc, LeaveRequestConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if isinstance(exc, LeaveRequestLifecycleError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    )


async def _commit_or_rollback(
    db: AsyncSession,
) -> None:
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/companies/{company_id}/leave-requests",
    response_model=list[LeaveRequestRead],
)
async def api_list_leave_requests(
    company_id: int,
    contract_id: int | None = Query(default=None),
    status_filter: LeaveRequestStatus | None = Query(
        default=None,
        alias="status",
    ),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _=Depends(
        require_company_permission(
            "employees.read"
        )
    ),
):
    try:
        return await list_leave_requests(
            db,
            company_id=company_id,
            contract_id=contract_id,
            status=status_filter,
            date_from=date_from,
            date_to=date_to,
        )
    except LeaveRequestError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/companies/{company_id}/leave-requests",
    response_model=LeaveRequestRead,
    status_code=status.HTTP_201_CREATED,
)
async def api_create_leave_request(
    company_id: int,
    data: LeaveRequestCreate,
    requested_by: int = Query(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(
        require_company_permission(
            "employees.manage"
        )
    ),
):
    try:
        row = await create_leave_request(
            db,
            company_id=company_id,
            requested_by=requested_by,
            data=data,
        )

        await _commit_or_rollback(db)
        await db.refresh(row)

        return row

    except LeaveRequestError as exc:
        await db.rollback()
        raise _http_error(exc) from exc


@router.get(
    "/companies/{company_id}/leave-requests/{leave_request_id}",
    response_model=LeaveRequestRead,
)
async def api_get_leave_request(
    company_id: int,
    leave_request_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(
        require_company_permission(
            "employees.read"
        )
    ),
):
    try:
        return await get_leave_request(
            db,
            company_id=company_id,
            leave_request_id=leave_request_id,
        )
    except LeaveRequestError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/companies/{company_id}/leave-requests/{leave_request_id}/approve",
    response_model=LeaveRequestRead,
)
async def api_approve_leave_request(
    company_id: int,
    leave_request_id: int,
    approved_by: int = Query(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(
        require_company_permission(
            "employees.manage"
        )
    ),
):
    try:
        row = await approve_leave_request(
            db,
            company_id=company_id,
            leave_request_id=leave_request_id,
            approved_by=approved_by,
        )

        await _commit_or_rollback(db)
        await db.refresh(row)

        return row

    except LeaveRequestError as exc:
        await db.rollback()
        raise _http_error(exc) from exc


@router.post(
    "/companies/{company_id}/leave-requests/{leave_request_id}/reject",
    response_model=LeaveRequestRead,
)
async def api_reject_leave_request(
    company_id: int,
    leave_request_id: int,
    data: LeaveRequestReject,
    rejected_by: int = Query(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(
        require_company_permission(
            "employees.manage"
        )
    ),
):
    try:
        row = await reject_leave_request(
            db,
            company_id=company_id,
            leave_request_id=leave_request_id,
            rejected_by=rejected_by,
            reason=data.reason,
        )

        await _commit_or_rollback(db)
        await db.refresh(row)

        return row

    except LeaveRequestError as exc:
        await db.rollback()
        raise _http_error(exc) from exc


@router.post(
    "/companies/{company_id}/leave-requests/{leave_request_id}/cancel",
    response_model=LeaveRequestRead,
)
async def api_cancel_leave_request(
    company_id: int,
    leave_request_id: int,
    data: LeaveRequestCancel,
    cancelled_by: int = Query(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(
        require_company_permission(
            "employees.manage"
        )
    ),
):
    try:
        row = await cancel_leave_request(
            db,
            company_id=company_id,
            leave_request_id=leave_request_id,
            cancelled_by=cancelled_by,
            reason=data.reason,
        )

        await _commit_or_rollback(db)
        await db.refresh(row)

        return row

    except LeaveRequestError as exc:
        await db.rollback()
        raise _http_error(exc) from exc


@router.get(
    "/companies/{company_id}/leave-requests/{leave_request_id}/history",
)
async def api_leave_request_history(
    company_id: int,
    leave_request_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(
        require_company_permission(
            "employees.read"
        )
    ),
):
    try:
        await get_leave_request(
            db,
            company_id=company_id,
            leave_request_id=leave_request_id,
        )
    except LeaveRequestError as exc:
        raise _http_error(exc) from exc

    result = await db.execute(
        select(HRChange)
        .where(
            HRChange.company_id == company_id,
            HRChange.entity_type == "leave_request",
            HRChange.entity_id == leave_request_id,
        )
        .order_by(
            HRChange.changed_at.asc(),
            HRChange.id.asc(),
        )
    )

    rows = list(result.scalars().all())

    return [
        {
            "id": row.id,
            "company_id": row.company_id,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "before_state": row.before_state,
            "after_state": row.after_state,
            "changed_by": row.changed_by,
            "changed_at": row.changed_at,
        }
        for row in rows
    ]
