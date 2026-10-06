from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.journal_entry import JournalEntryResponse
from app.schemas.payroll_advance import (
    PayrollAdvanceCreate,
    PayrollAdvanceRead,
)
from app.services.accounting_posting import AccountingPostingError
from app.services.accounting_reversal import AccountingReversalError
from app.services.payroll_advance_service import (
    reverse_payroll_advance_journal,
    generate_and_post_payroll_advance_journal,
    PayrollAdvanceError,
    create_payroll_advance,
    get_payroll_advance,
)


router = APIRouter(tags=["payroll-advances"])



def _advance_http_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/companies/{company_id}/payroll-periods/{payroll_period_id}"
    "/employment-contracts/{employment_contract_id}/advance",
    response_model=PayrollAdvanceRead,
)
async def read_payroll_advance(
    company_id: int,
    payroll_period_id: int,
    employment_contract_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    advance = await get_payroll_advance(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        employment_contract_id=employment_contract_id,
    )

    if advance is None:
        raise HTTPException(status_code=404, detail="Payroll advance not found")

    return advance


@router.post(
    "/companies/{company_id}/payroll-advances",
    response_model=PayrollAdvanceRead,
)
async def create_company_payroll_advance(
    company_id: int,
    payload: PayrollAdvanceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        advance = await create_payroll_advance(
            db,
            company_id=company_id,
            payroll_period_id=payload.payroll_period_id,
            employment_contract_id=payload.employment_contract_id,
            bank_account_id=payload.bank_account_id,
            advance_percentage=payload.advance_percentage,
            calculation_base_amount=payload.calculation_base_amount,
            minimum_due_amount=payload.minimum_due_amount,
            currency_code=payload.currency_code,
            payment_date=payload.payment_date,
            created_by=current_user.id,
        )

        await db.commit()
        await db.refresh(advance)
        return advance

    except PayrollAdvanceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post(
    "/companies/{company_id}/payroll-advances/"
    "{payroll_advance_id}/accounting-journal",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_post_payroll_advance_journal(
    company_id: int,
    payroll_advance_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: User = Depends(
        require_company_permission("journal_entries.create")
    ),
    approval: User = Depends(
        require_company_permission("journal_entries.approve")
    ),
):
    try:
        row = await generate_and_post_payroll_advance_journal(
            db,
            company_id=company_id,
            payroll_advance_id=payroll_advance_id,
            created_by=current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (
        PayrollAdvanceError,
        AccountingPostingError,
        AccountingReversalError,
    ) as exc:
        await db.rollback()
        raise _advance_http_error(exc) from exc
    except Exception:
        await db.rollback()
        raise


@router.post(
    "/companies/{company_id}/payroll-advances/"
    "{payroll_advance_id}/accounting-journal/reverse",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_reverse_payroll_advance_journal(
    company_id: int,
    payroll_advance_id: int,
    reversal_date: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: User = Depends(
        require_company_permission("journal_entries.reverse")
    ),
):
    try:
        row = await reverse_payroll_advance_journal(
            db,
            company_id=company_id,
            payroll_advance_id=payroll_advance_id,
            reversal_date=reversal_date,
            reversed_by=current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (
        PayrollAdvanceError,
        AccountingPostingError,
        AccountingReversalError,
    ) as exc:
        await db.rollback()
        raise _advance_http_error(exc) from exc
    except Exception:
        await db.rollback()
        raise
