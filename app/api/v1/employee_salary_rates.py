from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.employee_salary_rate import (
    SalaryRateCreate,
    SalaryRateRead,
    SalaryRateUpdate,
)
from app.services.employee_salary_rate_service import (
    create_salary_rate,
    get_salary_rate,
    list_salary_rates,
    update_salary_rate,
)


router = APIRouter(
    prefix="/companies/{company_id}",
)


@router.get(
    "/salary-rates",
    response_model=list[SalaryRateRead],
)
async def get_salary_rates(
    company_id: int,
    employment_contract_id: int | None = Query(default=None),
    employee_id: int | None = Query(default=None),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    return await list_salary_rates(
        db,
        company_id=company_id,
        employment_contract_id=employment_contract_id,
        employee_id=employee_id,
    )


@router.get(
    "/salary-rates/{salary_rate_id}",
    response_model=SalaryRateRead,
)
async def get_one_salary_rate(
    company_id: int,
    salary_rate_id: int,
    _: User = Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    return await get_salary_rate(
        db,
        company_id=company_id,
        salary_rate_id=salary_rate_id,
    )


@router.post(
    "/salary-rates",
    response_model=SalaryRateRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_salary_rate(
    company_id: int,
    data: SalaryRateCreate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await create_salary_rate(
            db,
            company_id=company_id,
            created_by=current_user.id,
            data=data,
        )
        await db.commit()
        await db.refresh(obj)
        return obj
    except HTTPException:
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Salary rate data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.patch(
    "/salary-rates/{salary_rate_id}",
    response_model=SalaryRateRead,
)
async def patch_salary_rate(
    company_id: int,
    salary_rate_id: int,
    data: SalaryRateUpdate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await update_salary_rate(
            db,
            company_id=company_id,
            salary_rate_id=salary_rate_id,
            changed_by=current_user.id,
            data=data,
        )
        await db.commit()
        await db.refresh(obj)
        return obj
    except HTTPException:
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Salary rate data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise
