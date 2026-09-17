"""Read-only VAT register; filters never suppress company control findings."""
from datetime import date, datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.schemas.vat_register import VatRegisterReport
from app.services.vat_register_service import get_vat_register, VatRegisterError

router = APIRouter(prefix='/companies/{company_id}/vat-registers', tags=['VAT registers'])


@router.get('', response_model=VatRegisterReport)
async def vat_register(
    company_id: int, date_from: date, date_to: date, as_of: datetime,
    direction: Literal['input','output'] | None = None,
    registration_status: Literal['prepared','submitted','registered','suspended','rejected','unknown'] | None = None,
    declaration_id: int | None = Query(default=None, gt=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_company_permission('journal_entries.read')),
):
    try:
        return await get_vat_register(db, company_id=company_id, date_from=date_from,
            date_to=date_to, as_of=as_of, direction=direction,
            registration_status=registration_status,declaration_id=declaration_id)
    except VatRegisterError as exc:
        raise HTTPException(422,str(exc)) from exc
