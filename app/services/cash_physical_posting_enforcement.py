"""Transition-safe physical cash posting enforcement.

All callers serialize on the same CashDesk row. Desks with
opening evidence require independent review and full GL
chronology certification. Legacy desks without evidence
remain outside physical balance certification.

Caller owns the transaction, commit and rollback.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash_desk import CashDesk
from app.models.payroll_disbursement import (
    CashDeskOpeningEvidence,
)
from app.services.cash_physical_balance_guard import (
    PhysicalCashBalanceGuardError,
    validate_locked_physical_cash,
)


class CashPostingEnforcementError(ValueError):
    pass


async def lock_cash_posting_scope(
    db: AsyncSession,
    *,
    company_id: int,
    cash_desk_id: int,
) -> bool:
    if (
        type(company_id) is not int
        or type(cash_desk_id) is not int
        or company_id <= 0
        or cash_desk_id <= 0
    ):
        raise CashPostingEnforcementError(
            "Invalid cash desk or company identity"
        )

    desk = await db.scalar(
        select(CashDesk)
        .where(
            CashDesk.company_id == company_id,
            CashDesk.id == cash_desk_id,
        )
        .with_for_update()
    )

    if desk is None:
        raise CashPostingEnforcementError(
            "Company cash desk not found"
        )

    evidence = await db.scalar(
        select(CashDeskOpeningEvidence.id).where(
            CashDeskOpeningEvidence.company_id == company_id,
            CashDeskOpeningEvidence.cash_desk_id == cash_desk_id,
        )
    )

    return evidence is not None


async def enforce_cash_posting_chronology(
    db: AsyncSession,
    *,
    company_id: int,
    cash_desk_id: int,
    as_of_date: date,
) -> bool:
    if type(as_of_date) is not date:
        raise CashPostingEnforcementError(
            "Invalid posting evaluation date"
        )

    enrolled = await lock_cash_posting_scope(
        db,
        company_id=company_id,
        cash_desk_id=cash_desk_id,
    )

    if not enrolled:
        raise CashPostingEnforcementError(
            "Independent physical cash opening evidence "
            "and review are required before cash posting"
        )

    try:
        await validate_locked_physical_cash(
            db,
            company_id=company_id,
            cash_desk_id=cash_desk_id,
            as_of_date=as_of_date,
        )
    except PhysicalCashBalanceGuardError as exc:
        raise CashPostingEnforcementError(
            "Reviewed physical cash chronology rejected operation"
        ) from exc

    return True
