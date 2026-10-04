from app.schemas.payroll_register import PayrollRegister
from app.services.payroll_register_service import get_payroll_register
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.payroll import PayrollPeriodStatus
from app.models.user import User
from app.schemas.payroll import (
    PayrollInputCreate,
    PayrollInputUpdate,
    PayrollInputRead,
    PayrollInputSalarySliceRead,
    PayrollPeriodCreate,
    PayrollPeriodRead,
)
from app.services.payroll_input_service import (
    PayrollInputConflictError,
    PayrollInputDerivationError,
    PayrollInputError,
    PayrollInputLifecycleError,
    PayrollInputNotFoundError,
    create_payroll_input,
    get_payroll_input,
    list_payroll_inputs,
    list_salary_slices,
    refresh_payroll_input,
    update_payroll_input_adjustment,
)
from app.services.payroll_period_service import (
    PayrollPeriodConflictError,
    PayrollPeriodError,
    PayrollPeriodLifecycleError,
    PayrollPeriodNotFoundError,
    create_payroll_period,
    finalize_payroll_period,
    get_payroll_period,
    list_payroll_periods,
)


router = APIRouter(
    prefix="/companies/{company_id}",
    tags=["payroll"],
)


def _period_http_error(
    exc: PayrollPeriodError,
) -> HTTPException:
    if isinstance(exc, PayrollPeriodNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            PayrollPeriodConflictError,
            PayrollPeriodLifecycleError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    )


def _input_http_error(
    exc: PayrollInputError,
) -> HTTPException:
    if isinstance(exc, PayrollInputNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            PayrollInputConflictError,
            PayrollInputLifecycleError,
            PayrollInputDerivationError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
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
    "/payroll-periods",
    response_model=list[PayrollPeriodRead],
)
async def api_list_payroll_periods(
    company_id: int,
    year: int | None = Query(default=None, gt=0),
    status_filter: PayrollPeriodStatus | None = Query(
        default=None,
        alias="status",
    ),
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await list_payroll_periods(
            db,
            company_id=company_id,
            year=year,
            status=status_filter,
        )
    except PayrollPeriodError as exc:
        raise _period_http_error(exc) from exc


@router.post(
    "/payroll-periods",
    response_model=PayrollPeriodRead,
    status_code=status.HTTP_201_CREATED,
)
async def api_create_payroll_period(
    company_id: int,
    data: PayrollPeriodCreate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await create_payroll_period(
            db,
            company_id=company_id,
            created_by=current_user.id,
            data=data,
        )

        await _commit_write(db)
        await db.refresh(row)

        return row
    except PayrollPeriodError as exc:
        await db.rollback()
        raise _period_http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payroll period data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/payroll-periods/{payroll_period_id}",
    response_model=PayrollPeriodRead,
)
async def api_get_payroll_period(
    company_id: int,
    payroll_period_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_payroll_period(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
        )
    except PayrollPeriodError as exc:
        raise _period_http_error(exc) from exc


@router.post(
    "/payroll-periods/{payroll_period_id}/finalize",
    response_model=PayrollPeriodRead,
)
async def api_finalize_payroll_period(
    company_id: int,
    payroll_period_id: int,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await finalize_payroll_period(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            finalized_by=current_user.id,
        )

        await _commit_write(db)
        await db.refresh(row)

        return row
    except PayrollPeriodError as exc:
        await db.rollback()
        raise _period_http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payroll period finalization conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/payroll-periods/{payroll_period_id}/inputs",
    response_model=list[PayrollInputRead],
)
async def api_list_payroll_inputs(
    company_id: int,
    payroll_period_id: int,
    contract_id: int | None = Query(default=None, gt=0),
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await list_payroll_inputs(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            contract_id=contract_id,
        )
    except PayrollInputError as exc:
        raise _input_http_error(exc) from exc


@router.post(
    "/payroll-periods/{payroll_period_id}/inputs",
    response_model=PayrollInputRead,
    status_code=status.HTTP_201_CREATED,
)
async def api_create_payroll_input(
    company_id: int,
    payroll_period_id: int,
    data: PayrollInputCreate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await create_payroll_input(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            created_by=current_user.id,
            data=data,
        )

        await _commit_write(db)
        await db.refresh(row)

        return row
    except PayrollInputError as exc:
        await db.rollback()
        raise _input_http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payroll input data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/payroll-inputs/{payroll_input_id}",
    response_model=PayrollInputRead,
)
async def api_get_payroll_input(
    company_id: int,
    payroll_input_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_payroll_input(
            db,
            company_id=company_id,
            payroll_input_id=payroll_input_id,
        )
    except PayrollInputError as exc:
        raise _input_http_error(exc) from exc


@router.post(
    "/payroll-inputs/{payroll_input_id}/refresh",
    response_model=PayrollInputRead,
)
async def api_refresh_payroll_input(
    company_id: int,
    payroll_input_id: int,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await refresh_payroll_input(
            db,
            company_id=company_id,
            payroll_input_id=payroll_input_id,
        )

        await _commit_write(db)
        await db.refresh(row)

        return row
    except PayrollInputError as exc:
        await db.rollback()
        raise _input_http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payroll input refresh conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.patch(
    "/payroll-inputs/{payroll_input_id}/adjustment",
    response_model=PayrollInputRead,
)
async def api_update_payroll_input_adjustment(
    company_id: int,
    payroll_input_id: int,
    data: PayrollInputUpdate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await update_payroll_input_adjustment(
            db,
            company_id=company_id,
            payroll_input_id=payroll_input_id,
            data=data,
        )

        await _commit_write(db)
        await db.refresh(row)

        return row
    except PayrollInputError as exc:
        await db.rollback()
        raise _input_http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payroll input adjustment conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/payroll-inputs/{payroll_input_id}/salary-slices",
    response_model=list[PayrollInputSalarySliceRead],
)
async def api_list_payroll_input_salary_slices(
    company_id: int,
    payroll_input_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await list_salary_slices(
            db,
            company_id=company_id,
            payroll_input_id=payroll_input_id,
        )
    except PayrollInputError as exc:
        raise _input_http_error(exc) from exc


@router.get('/payroll-periods/{payroll_period_id}/register', response_model=PayrollRegister)
async def read_payroll_register(
    company_id: int,
    payroll_period_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_company_permission('employees.read')),
):
    try:
        return await get_payroll_register(db, company_id=company_id,
                                          payroll_period_id=payroll_period_id)
    except PayrollPeriodError as exc:
        raise _period_http_error(exc) from exc
