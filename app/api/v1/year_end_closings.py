from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.journal_entry import JournalEntry
from app.models.user import User
from app.models.year_end_closing import YearEndClosing
from app.schemas.year_end_closing import YearEndClosingResponse, YearEndCloseRequest, YearEndPreview, YearEndPreviewRequest
from app.services.accounting_posting import AccountingPostingError
from app.services.accounting_reversal import AccountingReversalError
from app.services.year_end_closing_service import YearEndError, YearEndNotFound, close_year, get_closing, preview_year_end, reverse_year_end

router = APIRouter(prefix="/companies/{company_id}/year-end-closings", tags=["Year-end closings"])


async def _response(db, closing):
    result = YearEndClosingResponse.model_validate(closing)
    if closing.journal_entry_id is not None:
        result.reversal_journal_entry_id = await db.scalar(select(JournalEntry.id).where(
            JournalEntry.company_id == closing.company_id,
            JournalEntry.reversal_of_id == closing.journal_entry_id))
    return result


def _error(exc):
    if isinstance(exc, YearEndNotFound):
        return HTTPException(404,str(exc))
    if isinstance(exc, (YearEndError,AccountingPostingError,AccountingReversalError)):
        return HTTPException(409,str(exc))
    if isinstance(exc, IntegrityError):
        return HTTPException(409,"Year-end closing conflict")
    return exc


@router.post("/{year}/preview", response_model=YearEndPreview)
async def preview(company_id: int, data: YearEndPreviewRequest, year: int = Path(ge=2000,le=2100),
    user: User = Depends(require_company_permission("journal_entries.read")), db: AsyncSession = Depends(get_db)):
    try:
        return await preview_year_end(db,company_id,year,data)
    except YearEndError as exc:
        raise _error(exc) from exc


@router.post("/{year}/close", response_model=YearEndClosingResponse, status_code=201)
async def close(company_id: int, data: YearEndCloseRequest, year: int = Path(ge=2000,le=2100),
    user: User = Depends(require_company_permission("accounting.periods.manage")),
    posting_user: User = Depends(require_company_permission("journal_entries.post")),
    db: AsyncSession = Depends(get_db)):
    try:
        closing = await close_year(db,company_id,year,user.id,data)
        result = await _response(db,closing)
        await db.commit()
        return result
    except Exception as exc:
        await db.rollback()
        mapped = _error(exc)
        if mapped is exc:
            raise
        raise mapped from exc


@router.get("", response_model=list[YearEndClosingResponse])
async def list_closings(company_id: int, user: User = Depends(require_company_permission("journal_entries.read")),
    db: AsyncSession = Depends(get_db)):
    closings = (await db.scalars(select(YearEndClosing).where(YearEndClosing.company_id == company_id)
        .order_by(YearEndClosing.year.desc(),YearEndClosing.id.desc()))).all()
    return [await _response(db,c) for c in closings]


@router.get("/{closing_id}", response_model=YearEndClosingResponse)
async def read(company_id: int, closing_id: int, user: User = Depends(require_company_permission("journal_entries.read")),
    db: AsyncSession = Depends(get_db)):
    try:
        return await _response(db,await get_closing(db,company_id,closing_id))
    except YearEndError as exc:
        raise _error(exc) from exc


@router.post("/{closing_id}/reverse", response_model=YearEndClosingResponse)
async def reverse(company_id: int, closing_id: int,
    user: User = Depends(require_company_permission("accounting.periods.manage")),
    reversing_user: User = Depends(require_company_permission("journal_entries.reverse")),
    db: AsyncSession = Depends(get_db)):
    try:
        closing = await reverse_year_end(db,company_id,closing_id,user.id)
        result = await _response(db,closing)
        await db.commit()
        return result
    except Exception as exc:
        await db.rollback()
        mapped = _error(exc)
        if mapped is exc:
            raise
        raise mapped from exc
