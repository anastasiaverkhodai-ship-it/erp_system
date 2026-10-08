from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.payroll_leave_pay import (
    LeaveHistoryCalculationCreate, LeaveAverageHistoryRead, VacationCalculationRead,
    LeaveAverageSourceRead,
)
from app.services.payroll_average_history_service import (
    derive_leave_average_history, calculate_vacation_from_history,
    PayrollAverageHistoryError, PayrollAverageHistoryNotFoundError,
)
from app.services.payroll_vacation_service import (
    PayrollVacationError, PayrollVacationNotFoundError, get_vacation_calculation_for_leave,
    list_vacation_calculation_sources,
)

router = APIRouter(prefix='/companies/{company_id}/leave-requests/{leave_request_id}', tags=['Payroll leave pay'])


def error(exc):
    return HTTPException(status_code=404 if isinstance(exc, (PayrollAverageHistoryNotFoundError,
        PayrollVacationNotFoundError)) else 409, detail=str(exc))


async def response(db, row):
    await db.refresh(row)
    result = VacationCalculationRead.model_validate(row)
    result.sources = [LeaveAverageSourceRead.model_validate(s) for s in
        await list_vacation_calculation_sources(db, company_id=row.company_id, vacation_calculation_id=row.id)]
    return result


@router.get('/average-history', response_model=LeaveAverageHistoryRead)
async def read_average(company_id: int, leave_request_id: int,
    reference_period_start: date | None = None, reference_period_end: date | None = None,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.read'))):
    try:
        return await derive_leave_average_history(db, company_id=company_id, leave_request_id=leave_request_id,
            reference_period_start=reference_period_start, reference_period_end=reference_period_end)
    except PayrollAverageHistoryError as exc:
        raise error(exc) from exc


@router.post('/vacation-calculation/from-history', response_model=VacationCalculationRead)
async def create_vacation(company_id: int, leave_request_id: int, data: LeaveHistoryCalculationCreate,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.manage'))):
    try:
        row = await calculate_vacation_from_history(db, company_id=company_id, leave_request_id=leave_request_id,
            reference_period_start=data.reference_period_start, reference_period_end=data.reference_period_end,
            calculated_by=actor.id)
        result = await response(db, row)
        await db.commit()
        return result
    except (PayrollAverageHistoryError, PayrollVacationError) as exc:
        await db.rollback()
        raise error(exc) from exc


@router.get('/vacation-calculation', response_model=VacationCalculationRead)
async def read_vacation(company_id: int, leave_request_id: int,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.read'))):
    row = await get_vacation_calculation_for_leave(db, company_id=company_id, leave_request_id=leave_request_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Vacation calculation not found')
    return await response(db, row)
