from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.payroll_leave_balance import (
    LeaveAccountCreate, LeaveAccountRead, LeaveAccrualCreate,
    LeaveAllocationCreate, LeaveBalanceEntryRead, LeaveBalanceRead,
)
from app.services.payroll_leave_balance_service import (
    create_leave_account, accrue_leave_days, allocate_leave_days,
    leave_balances, LeaveBalanceError,
)
router=APIRouter(prefix='/companies/{company_id}',tags=['Leave day balances'])


@router.post('/leave-accounts',response_model=LeaveAccountRead,status_code=201)
async def create_account(company_id:int,data:LeaveAccountCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    try:
        row=await create_leave_account(db,company_id=company_id,data=data,actor_id=actor.id)
        await db.commit();await db.refresh(row)
        return row
    except LeaveBalanceError as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.post('/leave-accounts/{account_id}/accruals',response_model=list[LeaveBalanceEntryRead])
async def accrue(company_id:int,account_id:int,data:LeaveAccrualCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    try:
        rows=await accrue_leave_days(db,company_id=company_id,account_id=account_id,data=data,actor_id=actor.id)
        result=[LeaveBalanceEntryRead.model_validate(row) for row in rows]
        await db.commit();return result
    except LeaveBalanceError as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.post('/leave-requests/{leave_request_id}/day-allocations',response_model=list[LeaveBalanceEntryRead])
async def allocate(company_id:int,leave_request_id:int,data:LeaveAllocationCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    try:
        rows=await allocate_leave_days(db,company_id=company_id,leave_request_id=leave_request_id,
            allocations=[item.model_dump() for item in data.allocations],request_key=data.request_key,
            source_reference=data.source_reference,actor_id=actor.id)
        result=[LeaveBalanceEntryRead.model_validate(row) for row in rows]
        await db.commit();return result
    except LeaveBalanceError as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.get('/employment-contracts/{contract_id}/leave-balances',response_model=list[LeaveBalanceRead])
async def balances(company_id:int,contract_id:int,as_of:date,db:AsyncSession=Depends(get_db),
    _:User=Depends(require_company_permission('employees.read'))):
    try:
        return await leave_balances(db,company_id=company_id,employment_contract_id=contract_id,as_of=as_of)
    except LeaveBalanceError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


from app.schemas.payroll_leave_compensation import LeaveCompensationCreate, LeaveCompensationRead, LeaveCompensationLineRead
from app.services.payroll_leave_compensation_service import create_leave_compensation
from app.services.payroll_average_history_service import PayrollAverageHistoryError
from app.models.payroll_leave_compensation import PayrollLeaveCompensation, PayrollLeaveCompensationLine
from sqlalchemy import select


@router.post('/leave-compensations',response_model=LeaveCompensationRead,status_code=201)
async def compensate(company_id:int,data:LeaveCompensationCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    try:
        row=await create_leave_compensation(db,company_id=company_id,data=data,actor_id=actor.id)
        result=LeaveCompensationRead.model_validate(row)
        await db.commit();return result
    except (LeaveBalanceError,PayrollAverageHistoryError) as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.get('/leave-compensations/{compensation_id}',response_model=LeaveCompensationRead)
async def compensation(company_id:int,compensation_id:int,db:AsyncSession=Depends(get_db),
    _:User=Depends(require_company_permission('employees.read'))):
    row=await db.scalar(select(PayrollLeaveCompensation).where(PayrollLeaveCompensation.company_id==company_id,
        PayrollLeaveCompensation.id==compensation_id))
    if row is None:
        raise HTTPException(status_code=404,detail='Leave compensation not found')
    from decimal import Decimal
    from app.services.payroll_leave_correction_sources import latest_leave_correction
    result=LeaveCompensationRead.model_validate(row)
    correction=await latest_leave_correction(db,company_id=company_id,target_kind='compensation',target_id=row.id)
    if correction:
        result.correction_id=correction.id
        result.additional_lines=[item for item in correction.replacement_snapshot['lines'] if item.get('source_kind')=='extra']
        result.effective_gross_amount=sum((Decimal(item['amount']) for item in correction.replacement_snapshot['lines']),Decimal(0))
    return result


@router.get('/leave-compensations/{compensation_id}/lines',response_model=list[LeaveCompensationLineRead])
async def compensation_lines(company_id:int,compensation_id:int,db:AsyncSession=Depends(get_db),
    _:User=Depends(require_company_permission('employees.read'))):
    if await db.scalar(select(PayrollLeaveCompensation.id).where(PayrollLeaveCompensation.company_id==company_id,
        PayrollLeaveCompensation.id==compensation_id)) is None:
        raise HTTPException(status_code=404,detail='Leave compensation not found')
    rows=list((await db.scalars(select(PayrollLeaveCompensationLine).where(
        PayrollLeaveCompensationLine.company_id==company_id,PayrollLeaveCompensationLine.compensation_id==compensation_id)
        .order_by(PayrollLeaveCompensationLine.id))).all())
    from decimal import Decimal
    from app.services.payroll_leave_correction_sources import latest_leave_correction
    correction=await latest_leave_correction(db,company_id=company_id,target_kind='compensation',target_id=compensation_id)
    effective={item['source_id']:item for item in correction.replacement_snapshot['lines'] if item.get('source_kind','original')=='original'} if correction else {}
    result=[]
    for row in rows:
        item=LeaveCompensationLineRead.model_validate(row)
        if correction:
            current=effective[row.id]
            item.correction_id=correction.id
            item.effective_amount=Decimal(current['amount'])
            item.effective_compensated_days=Decimal(current['quantity'])
            item.effective_consumed_days=Decimal(current.get('consumed_days',str(row.consumed_days)))
        result.append(item)
    return result


from app.schemas.payroll_leave_compensation import LeaveCompensationReverse, LeaveCompensationReversalRead
from app.services.payroll_leave_compensation_service import reverse_leave_compensation


@router.post('/leave-compensations/{compensation_id}/reversals', response_model=LeaveCompensationReversalRead, status_code=201)
async def reverse_compensation(company_id: int, compensation_id: int, data: LeaveCompensationReverse,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.manage'))):
    try:
        row = await reverse_leave_compensation(db, company_id=company_id,
            compensation_id=compensation_id, data=data, actor_id=actor.id)
        result = LeaveCompensationReversalRead.model_validate(row)
        await db.commit()
        return result
    except LeaveBalanceError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
