from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.schemas.general_ledger import AccountCardReport, GeneralLedgerReport
from app.services.general_ledger_service import (
    get_account_card,
    get_general_ledger,
)


router = APIRouter(
    prefix="/companies/{company_id}",
    tags=["General Ledger"],
)


@router.get(
    "/general-ledger",
    response_model=GeneralLedgerReport,
)
async def read_general_ledger(
    company_id: int,
    date_from: date = Query(...),
    date_to: date = Query(...),
    account_id: int | None = Query(default=None),
    account_code: str | None = Query(default=None),
    _=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_general_ledger(
            db,
            company_id=company_id,
            date_from=date_from,
            date_to=date_to,
            account_id=account_id,
            account_code=account_code,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.get(
    "/accounts/{account_id}/card",
    response_model=AccountCardReport,
)
async def read_account_card(
    company_id: int,
    account_id: int,
    date_from: date = Query(...),
    date_to: date = Query(...),
    _=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_account_card(
            db,
            company_id=company_id,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
        )
    except ValueError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message,
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=message,
        ) from exc
