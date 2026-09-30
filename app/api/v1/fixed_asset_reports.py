from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import engine
from app.schemas.fixed_asset_report import FixedAssetMovementReport, FixedAssetGLReconciliation, FixedAssetDepreciationReport
from app.services.fixed_asset_report_service import get_fixed_asset_movement, get_fixed_asset_gl_reconciliation, get_fixed_asset_depreciation_report

async def get_report_db():
    # Permission checks use their own ordinary session. All report queries see
    # one immutable PostgreSQL snapshot, including concurrent manual postings.
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level='REPEATABLE READ', postgresql_readonly=True)
        async with AsyncSession(bind=connection) as session:
            yield session


router = APIRouter(prefix='/companies/{company_id}/fixed-asset-reports', tags=['Fixed asset reports'],
    dependencies=[Depends(require_company_permission('journal_entries.read'))])


async def _report(service, db, **kwargs):
    try:
        return await service(db, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/movement', response_model=FixedAssetMovementReport)
async def movement(company_id: int, date_from: date = Query(...), date_to: date = Query(...),
                   fixed_asset_id: int | None = Query(None, gt=0), db: AsyncSession = Depends(get_report_db)):
    return await _report(get_fixed_asset_movement, db, company_id=company_id, date_from=date_from,
                         date_to=date_to, fixed_asset_id=fixed_asset_id)


@router.get('/depreciation', response_model=FixedAssetDepreciationReport)
async def depreciation(company_id: int, date_from: date = Query(...), date_to: date = Query(...),
                       fixed_asset_id: int | None = Query(None, gt=0), db: AsyncSession = Depends(get_report_db)):
    return await _report(get_fixed_asset_depreciation_report, db, company_id=company_id,
                         date_from=date_from, date_to=date_to, fixed_asset_id=fixed_asset_id)


@router.get('/gl-reconciliation', response_model=FixedAssetGLReconciliation)
async def reconciliation(company_id: int, date_from: date = Query(...), date_to: date = Query(...),
                         db: AsyncSession = Depends(get_report_db)):
    return await _report(get_fixed_asset_gl_reconciliation, db, company_id=company_id,
                         date_from=date_from, date_to=date_to)
