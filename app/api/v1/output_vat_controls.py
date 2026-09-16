"""Read-only OUTPUT recognition/GL control, scoped by company permission."""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.services.accounting_account_role_resolver import AccountingAccountRoleResolutionError
from app.services.output_vat_gl_reconciliation_service import (
    OutputVatGlReconciliation, OutputVatGlReconciliationError, reconcile_output_vat_gl,
)

router = APIRouter(prefix='/companies/{company_id}/vat-controls', tags=['VAT controls'])


@router.get('/output-gl', response_model=OutputVatGlReconciliation)
async def output_gl(company_id: int, date_from: date, date_to: date,
                    db: AsyncSession = Depends(get_db),
                    _=Depends(require_company_permission('journal_entries.read'))):
    try:
        return await reconcile_output_vat_gl(db, company_id=company_id, date_from=date_from, date_to=date_to)
    except OutputVatGlReconciliationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except AccountingAccountRoleResolutionError as exc:
        raise HTTPException(409, str(exc)) from exc
