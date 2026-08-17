from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting_period import AccountingPeriod


async def ensure_period_open(
    company_id: int,
    operation_date: date,
    db: AsyncSession,
) -> AccountingPeriod:
    result = await db.execute(
        select(AccountingPeriod).where(
            AccountingPeriod.company_id == company_id,
            AccountingPeriod.start_date <= operation_date,
            AccountingPeriod.end_date >= operation_date,
        )
    )

    period = result.scalar_one_or_none()

    if period is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Accounting period does not exist for this date",
        )

    if period.is_locked or period.status != "open":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Accounting period is closed",
        )

    return period