from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.journal_entry import JournalEntryResponse
from app.schemas.payroll_disbursement import (
    PayrollDisbursementCreate,
    PayrollDisbursementRead,
)
from app.services.payroll_disbursement_service import (
    PayrollDisbursementError,
    PayrollDisbursementNotFoundError,
    create_payroll_disbursement,
    generate_and_post_payroll_disbursement_journal,
    get_payroll_disbursement,
    list_payroll_disbursements,
    cancel_payroll_disbursement,
    reverse_payroll_disbursement_journal,
)


from app.services.accounting_posting import AccountingPostingError
from app.services.accounting_reversal import AccountingReversalError

router = APIRouter(tags=["payroll-disbursements"])


def _http_error(exc: PayrollDisbursementError) -> HTTPException:
    if isinstance(exc, PayrollDisbursementNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(exc),
    )


@router.get(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/disbursement",
    response_model=PayrollDisbursementRead,
)
async def api_get_payroll_disbursement(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("journal_entries.read")
    ),
):
    row = await get_payroll_disbursement(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll disbursement not found",
        )
    return row


@router.post(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/disbursement",
    response_model=PayrollDisbursementRead,
    status_code=status.HTTP_201_CREATED,
)
async def api_create_payroll_disbursement(
    company_id: int,
    payroll_calculation_id: int,
    payload: PayrollDisbursementCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: User = Depends(
        require_company_permission("journal_entries.create")
    ),
):
    try:
        row = await create_payroll_disbursement(
            db,
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
            bank_account_id=payload.bank_account_id,
            payment_date=payload.payment_date,
            created_by=current_user.id,
            amount=payload.amount,
            request_key=payload.request_key,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (PayrollDisbursementError, AccountingPostingError, AccountingReversalError) as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        await db.rollback()
        raise


@router.post(
    "/companies/{company_id}/payroll-disbursements/"
    "{payroll_disbursement_id}/accounting-journal",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_post_payroll_disbursement_journal(
    company_id: int,
    payroll_disbursement_id: int,
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
        row = await generate_and_post_payroll_disbursement_journal(
            db,
            company_id=company_id,
            payroll_disbursement_id=payroll_disbursement_id,
            created_by=current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (PayrollDisbursementError, AccountingPostingError, AccountingReversalError) as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        await db.rollback()
        raise


@router.post(
    "/companies/{company_id}/payroll-disbursements/"
    "{payroll_disbursement_id}/accounting-journal/reverse",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_reverse_payroll_disbursement_journal(
    company_id: int,
    payroll_disbursement_id: int,
    reversal_date: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: User = Depends(
        require_company_permission("journal_entries.reverse")
    ),
):
    try:
        row = await reverse_payroll_disbursement_journal(
            db,
            company_id=company_id,
            payroll_disbursement_id=payroll_disbursement_id,
            reversal_date=reversal_date,
            reversed_by=current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (PayrollDisbursementError, AccountingPostingError, AccountingReversalError) as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        await db.rollback()
        raise


@router.get('/companies/{company_id}/payroll-calculations/{payroll_calculation_id}/disbursements',
            response_model=list[PayrollDisbursementRead])
async def api_list_payroll_disbursements(company_id: int, payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_company_permission('journal_entries.read'))):
    return await list_payroll_disbursements(db, company_id=company_id,
        payroll_calculation_id=payroll_calculation_id)


@router.post('/companies/{company_id}/payroll-disbursements/{payroll_disbursement_id}/cancel',
             response_model=PayrollDisbursementRead)
async def api_cancel_payroll_disbursement(company_id: int, payroll_disbursement_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_company_permission('journal_entries.delete'))):
    try:
        row = await cancel_payroll_disbursement(db, company_id=company_id,
            payroll_disbursement_id=payroll_disbursement_id, cancelled_by=actor.id)
        await db.commit()
        await db.refresh(row)
        return row
    except PayrollDisbursementError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        await db.rollback()
        raise


from app.schemas.payroll_disbursement import PayrollBankMatchCreate, PayrollBankMatchRead
from app.services.payroll_bank_reconciliation_service import (
    reconcile_payroll_disbursement, unmatch_payroll_disbursement,
)
from app.services.bank_statement_reconciliation_service import BankStatementReconciliationError


@router.post('/companies/{company_id}/payroll-disbursements/{payroll_disbursement_id}/bank-reconciliations',
             response_model=PayrollBankMatchRead)
async def api_match_payroll_bank(company_id: int, payroll_disbursement_id: int,
    payload: PayrollBankMatchCreate, db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_company_permission('payments.manage'))):
    try:
        event = await reconcile_payroll_disbursement(db, company_id=company_id,
            payroll_disbursement_id=payroll_disbursement_id, created_by=actor.id,
            **payload.model_dump())
        await db.commit()
        await db.refresh(event)
        return event
    except BankStatementReconciliationError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise


@router.post('/companies/{company_id}/payroll-disbursements/{payroll_disbursement_id}/bank-reconciliations/{reconciliation_id}/reverse',
             response_model=PayrollBankMatchRead)
async def api_unmatch_payroll_bank(company_id: int, payroll_disbursement_id: int,
    reconciliation_id: int, db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_company_permission('payments.manage'))):
    try:
        event = await unmatch_payroll_disbursement(db, company_id=company_id,
            payroll_disbursement_id=payroll_disbursement_id,
            reconciliation_id=reconciliation_id, reversed_by=actor.id)
        await db.commit()
        await db.refresh(event)
        return event
    except BankStatementReconciliationError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise
