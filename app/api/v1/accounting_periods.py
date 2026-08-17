import calendar
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.accounting_period import AccountingPeriod
from app.models.user import User
from app.schemas.accounting_period import (
    AccountingPeriodCreate,
    AccountingPeriodResponse,
)



router = APIRouter(
    prefix="/companies/{company_id}/accounting-periods",
    tags=["Accounting Periods"],
)


@router.get(
    "/",
    response_model=list[AccountingPeriodResponse],
)
async def get_accounting_periods(
    company_id: int,
    current_user: User = Depends(
        require_company_permission("accounting.periods.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AccountingPeriod)
        .where(
            AccountingPeriod.company_id == company_id
        )
        .order_by(
            AccountingPeriod.year.desc(),
            AccountingPeriod.month.desc(),
        )
    )

    return result.scalars().all()


@router.post(
    "/",
    response_model=AccountingPeriodResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_accounting_period(
    company_id: int,
    data: AccountingPeriodCreate,
    current_user: User = Depends(
        require_company_permission("accounting.periods.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    existing_result = await db.execute(
        select(AccountingPeriod).where(
            AccountingPeriod.company_id == company_id,
            AccountingPeriod.year == data.year,
            AccountingPeriod.month == data.month,
        )
    )

    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Accounting period already exists",
        )

    last_day = calendar.monthrange(
        data.year,
        data.month,
    )[1]

    period = AccountingPeriod(
        company_id=company_id,
        year=data.year,
        month=data.month,
        start_date=date(
            data.year,
            data.month,
            1,
        ),
        end_date=date(
            data.year,
            data.month,
            last_day,
        ),
        status="open",
        is_locked=False,
    )

    db.add(period)

    await db.commit()
    await db.refresh(period)

    return period

@router.patch(
    "/{period_id}/close",
    response_model=AccountingPeriodResponse,
)
async def close_accounting_period(
    company_id: int,
    period_id: int,
    current_user: User = Depends(
        require_company_permission("accounting.periods.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AccountingPeriod).where(
            AccountingPeriod.id == period_id,
            AccountingPeriod.company_id == company_id,
        )
    )

    period = result.scalar_one_or_none()

    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Accounting period not found",
        )

    if period.is_locked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Accounting period is already closed",
        )

    period.status = "closed"
    period.is_locked = True
    period.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    await db.commit()
    await db.refresh(period)

    return period

@router.patch(
    "/{period_id}/reopen",
    response_model=AccountingPeriodResponse,
)
async def reopen_accounting_period(
    company_id: int,
    period_id: int,
    current_user: User = Depends(
        require_company_permission("accounting.periods.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AccountingPeriod).where(
            AccountingPeriod.id == period_id,
            AccountingPeriod.company_id == company_id,
        )
    )

    period = result.scalar_one_or_none()

    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Accounting period not found",
        )

    if not period.is_locked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Accounting period is already open",
        )

    period.status = "open"
    period.is_locked = False
    period.closed_at = None

    await db.commit()
    await db.refresh(period)

    return period