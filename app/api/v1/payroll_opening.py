from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.models.payroll_opening import PayrollEarningsHistory, PayrollLeaveOpening
from app.schemas.payroll_opening import (PayrollOpeningAttach, PayrollOpeningDebtRead,
    PayrollEarningsHistoryInput, PayrollEarningsHistoryRead, PayrollLeaveOpeningInput, PayrollLeaveOpeningRead)
from app.services.payroll_opening_service import (PayrollOpeningError, PayrollOpeningNotFoundError,
    attach_payroll_opening,list_payroll_opening_debts,import_earnings_history,import_leave_opening,_contract)
router=APIRouter(prefix='/companies/{company_id}',tags=['Payroll opening inputs'])


def _error(exc):
    return HTTPException(status_code=404 if isinstance(exc,PayrollOpeningNotFoundError) else 409,detail=str(exc))


@router.post('/opening-balances/{opening_balance_id}/payroll-detail',response_model=list[PayrollOpeningDebtRead],status_code=201)
async def attach_detail(company_id:int,opening_balance_id:int,data:PayrollOpeningAttach,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage')),
    _:User=Depends(require_company_permission('journal_entries.create'))):
    try:
        await attach_payroll_opening(db,company_id=company_id,opening_balance_id=opening_balance_id,data=data,created_by=actor.id)
        rows=await list_payroll_opening_debts(db,company_id=company_id,opening_balance_id=opening_balance_id)
        result=[PayrollOpeningDebtRead.model_validate(row) for row in rows]
        await db.commit();return result
    except PayrollOpeningError as exc:
        await db.rollback();raise _error(exc) from exc
    except Exception:
        await db.rollback();raise


@router.get('/opening-balances/{opening_balance_id}/payroll-detail',response_model=list[PayrollOpeningDebtRead])
async def read_detail(company_id:int,opening_balance_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    return await list_payroll_opening_debts(db,company_id=company_id,opening_balance_id=opening_balance_id)


async def _import(db,operation,schema):
    try:
        result=schema.model_validate(await operation)
        await db.commit();return result
    except PayrollOpeningError as exc:
        await db.rollback();raise _error(exc) from exc
    except Exception:
        await db.rollback();raise


@router.post('/employment-contracts/{contract_id}/earnings-history',response_model=PayrollEarningsHistoryRead,status_code=201)
async def post_earnings_history(company_id:int,contract_id:int,data:PayrollEarningsHistoryInput,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    return await _import(db,import_earnings_history(db,company_id=company_id,contract_id=contract_id,
        data=data,created_by=actor.id),PayrollEarningsHistoryRead)


@router.post('/employment-contracts/{contract_id}/leave-opening',response_model=PayrollLeaveOpeningRead,status_code=201)
async def post_leave_opening(company_id:int,contract_id:int,data:PayrollLeaveOpeningInput,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    return await _import(db,import_leave_opening(db,company_id=company_id,contract_id=contract_id,
        data=data,created_by=actor.id),PayrollLeaveOpeningRead)


@router.get('/employment-contracts/{contract_id}/earnings-history',response_model=list[PayrollEarningsHistoryRead])
async def read_earnings_history(company_id:int,contract_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    try: await _contract(db,company_id,contract_id)
    except PayrollOpeningError as exc: raise _error(exc) from exc
    return list((await db.scalars(select(PayrollEarningsHistory).where(PayrollEarningsHistory.company_id==company_id,
        PayrollEarningsHistory.employment_contract_id==contract_id).order_by(PayrollEarningsHistory.month))).all())


@router.get('/employment-contracts/{contract_id}/leave-opening',response_model=list[PayrollLeaveOpeningRead])
async def read_leave_opening(company_id:int,contract_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    try: await _contract(db,company_id,contract_id)
    except PayrollOpeningError as exc: raise _error(exc) from exc
    return list((await db.scalars(select(PayrollLeaveOpening).where(PayrollLeaveOpening.company_id==company_id,
        PayrollLeaveOpening.employment_contract_id==contract_id).order_by(PayrollLeaveOpening.working_year_start))).all())


from datetime import date
from app.services.payroll_employee_balance_service import employee_payroll_balance


@router.get('/employees/{employee_id}/payroll-balance')
async def read_employee_balance(company_id:int,employee_id:int,as_of:date,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    try:
        return await employee_payroll_balance(db,company_id=company_id,employee_id=employee_id,as_of=as_of)
    except PayrollOpeningError as exc:
        raise _error(exc) from exc
