from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.journal_entry import JournalEntryResponse
from app.services.payroll_accounting_service import (
    PayrollAccountingDuplicateError,
    PayrollAccountingError,
    PayrollAccountingNotFoundError,
    PayrollAccountingSourceStateError,
    generate_and_post_payroll_journal_entry,
    get_payroll_accounting_journal,
    reverse_payroll_journal_entry,
)


router = APIRouter(
    tags=["payroll-accounting"],
)


def _http_error(
    exc: PayrollAccountingError,
) -> HTTPException:
    if isinstance(
        exc,
        PayrollAccountingNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        PayrollAccountingDuplicateError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if isinstance(
        exc,
        PayrollAccountingSourceStateError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=str(exc),
    )


@router.get(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/accounting-journal",
    response_model=JournalEntryResponse,
)
async def api_get_payroll_accounting_journal(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    row = await get_payroll_accounting_journal(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll accounting journal not found",
        )

    return row


@router.post(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/accounting-journal",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_post_payroll_accounting_journal(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: User = Depends(
        require_company_permission(
            "journal_entries.create"
        )
    ),
):
    try:
        row = (
            await generate_and_post_payroll_journal_entry(
                db,
                company_id=company_id,
                payroll_calculation_id=(
                    payroll_calculation_id
                ),
                created_by=current_user.id,
            )
        )

        await db.commit()
        await db.refresh(row)

        return row

    except PayrollAccountingError as exc:
        await db.rollback()
        raise _http_error(exc) from exc

    except Exception:
        await db.rollback()
        raise


@router.post(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/accounting-journal/reverse",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_reverse_payroll_accounting_journal(
    company_id: int,
    payroll_calculation_id: int,
    reversal_date: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: User = Depends(
        require_company_permission(
            "journal_entries.reverse"
        )
    ),
):
    try:
        row = await reverse_payroll_journal_entry(
            db,
            company_id=company_id,
            payroll_calculation_id=(
                payroll_calculation_id
            ),
            reversal_date=reversal_date,
            reversed_by=current_user.id,
        )

        await db.commit()
        await db.refresh(row)

        return row

    except PayrollAccountingError as exc:
        await db.rollback()
        raise _http_error(exc) from exc

    except Exception:
        await db.rollback()
        raise
