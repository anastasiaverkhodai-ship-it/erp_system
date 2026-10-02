from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.payroll_calculation import (
    PayrollCalculationLineRead,
    PayrollCalculationRead,
)
from app.services.payroll_calculation_service import (
    PayrollCalculationConflictError,
    PayrollCalculationDerivationError,
    PayrollCalculationError,
    PayrollCalculationLifecycleError,
    PayrollCalculationNotFoundError,
    calculate_payroll_input,
    get_payroll_calculation,
    list_payroll_calculation_lines,
    list_payroll_calculations,
)


router = APIRouter(tags=["payroll-calculations"])


def _raise_http(exc: PayrollCalculationError) -> None:
    if isinstance(exc, PayrollCalculationNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            PayrollCalculationConflictError,
            PayrollCalculationLifecycleError,
            PayrollCalculationDerivationError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    )


@router.get(
    "/companies/{company_id}/payroll-periods/"
    "{payroll_period_id}/calculations",
    response_model=list[PayrollCalculationRead],
)
async def list_period_payroll_calculations(
    company_id: int,
    payroll_period_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
):
    try:
        return await list_payroll_calculations(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
        )
    except PayrollCalculationError as exc:
        _raise_http(exc)


@router.post(
    "/companies/{company_id}/payroll-inputs/"
    "{payroll_input_id}/calculate",
    response_model=PayrollCalculationRead,
    status_code=status.HTTP_200_OK,
)
async def calculate_company_payroll_input(
    company_id: int,
    payroll_input_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: User = Depends(
        require_company_permission("employees.manage")
    ),
):
    try:
        calculation = await calculate_payroll_input(
            db,
            company_id=company_id,
            payroll_input_id=payroll_input_id,
            calculated_by=current_user.id,
        )

        await db.commit()
        await db.refresh(calculation)

        return calculation

    except PayrollCalculationError as exc:
        await db.rollback()
        _raise_http(exc)

    except Exception:
        await db.rollback()
        raise


@router.get(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}",
    response_model=PayrollCalculationRead,
)
async def get_company_payroll_calculation(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
):
    try:
        return await get_payroll_calculation(
            db,
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
        )
    except PayrollCalculationError as exc:
        _raise_http(exc)


@router.get(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/lines",
    response_model=list[PayrollCalculationLineRead],
)
async def list_company_payroll_calculation_lines(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
):
    try:
        return await list_payroll_calculation_lines(
            db,
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
        )
    except PayrollCalculationError as exc:
        _raise_http(exc)
