from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.schemas.accounting_controls import (
    ConsolidatedAccountingControlReport,
)
from app.services.accounting_account_role_resolver import AccountingAccountRoleResolutionError
from app.services.accounting_control_service import (
    get_consolidated_accounting_controls,
)
from app.services.output_vat_gl_reconciliation_service import (
    OutputVatGlReconciliationError,
)


async def accounting_control_snapshot(db: AsyncSession = Depends(get_db)):
    # Run before authorization queries: all family/bridge reads share one snapshot.
    # GET cannot alter the business state, including through future helper changes.
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))


router = APIRouter(
    prefix="/companies/{company_id}",
    tags=["Accounting controls"],
    dependencies=[Depends(accounting_control_snapshot)],
)


@router.get(
    "/accounting-controls",
    response_model=ConsolidatedAccountingControlReport,
)
async def read_accounting_controls(
    company_id: int,
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
        return await get_consolidated_accounting_controls(
            db,
            company_id=company_id,
            date_from=date_from,
            date_to=date_to,
        )
    except (
        ValueError,
        OutputVatGlReconciliationError,
    ) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except AccountingAccountRoleResolutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
