from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.permissions import require_company_permission
from app.schemas.trial_balance import TrialBalanceReport
from app.services.trial_balance_service import get_trial_balance


router = APIRouter(
    prefix="/companies/{company_id}",
    tags=["Trial Balance"],
)


@router.get(
    "/trial-balance",
    response_model=TrialBalanceReport,
)
async def read_trial_balance(
    company_id: int,
    date_from: date = Query(...),
    date_to: date = Query(...),
    _=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
) -> TrialBalanceReport:
    try:
        return await get_trial_balance(
            db,
            company_id=company_id,
            date_from=date_from,
            date_to=date_to,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(exc),
        ) from exc
