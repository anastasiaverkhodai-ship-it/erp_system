from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.payroll_statutory import (
    PayrollStatutoryRateCreate,
    PayrollStatutoryRateRead,
    PayrollStatutoryResultLineRead,
    PayrollStatutoryResultRead,
)
from app.services.payroll_statutory_service import (
    PayrollStatutoryError,
    calculate_payroll_statutory_result,
    list_statutory_rates,
    create_statutory_rate,
    get_statutory_result_for_calculation,
    list_statutory_result_lines,
)

router = APIRouter(tags=["payroll-statutory"])


def _raise_statutory_http(
    exc: PayrollStatutoryError,
) -> None:
    message = str(exc)
    lowered = message.lower()

    if "not found" in lowered:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=message,
        )

    if (
        "conflict" in lowered
        or "already exists" in lowered
        or "overlap" in lowered
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=message,
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=message,
    )


@router.post(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/statutory/calculate",
    response_model=PayrollStatutoryResultRead,
    status_code=status.HTTP_200_OK,
)
async def calculate_company_payroll_statutory_result(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
):
    try:
        result = await calculate_payroll_statutory_result(
            db,
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
            actor_user_id=current_user.id,
        )

        await db.commit()
        await db.refresh(result)

        return result

    except PayrollStatutoryError as exc:
        await db.rollback()
        _raise_statutory_http(exc)

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payroll statutory calculation conflict",
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.get(
    "/companies/{company_id}/payroll-statutory-rates",
    response_model=list[PayrollStatutoryRateRead],
)
async def list_company_payroll_statutory_rates(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
):
    try:
        return await list_statutory_rates(
            db,
            company_id=company_id,
        )
    except PayrollStatutoryError as exc:
        _raise_statutory_http(exc)


@router.post(
    "/companies/{company_id}/payroll-statutory-rates",
    response_model=PayrollStatutoryRateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_company_payroll_statutory_rate(
    company_id: int,
    data: PayrollStatutoryRateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
):
    try:
        row = await create_statutory_rate(
            db,
            company_id=company_id,
            employee_id=data.employee_id,
            source_reference=data.source_reference,
            tax_evidence_id=data.tax_evidence_id,
            component=data.component,
            rate=data.rate,
            effective_from=data.effective_from,
            effective_to=data.effective_to,
            actor_user_id=current_user.id,
        )

        await db.commit()
        await db.refresh(row)

        return row

    except PayrollStatutoryError as exc:
        await db.rollback()
        _raise_statutory_http(exc)

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payroll statutory rate conflict",
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.get(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/statutory",
    response_model=PayrollStatutoryResultRead,
)
async def get_company_payroll_statutory_result(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
):
    try:
        result = await get_statutory_result_for_calculation(
            db,
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Payroll statutory result not found")
        return result
    except PayrollStatutoryError as exc:
        _raise_statutory_http(exc)


@router.get(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/statutory/lines",
    response_model=list[PayrollStatutoryResultLineRead],
)
async def list_company_payroll_statutory_result_lines(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
):
    try:
        result = await get_statutory_result_for_calculation(
            db, company_id=company_id, payroll_calculation_id=payroll_calculation_id,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Payroll statutory result not found")
        return await list_statutory_result_lines(
            db, company_id=company_id, statutory_result_id=result.id,
        )
    except PayrollStatutoryError as exc:
        _raise_statutory_http(exc)
