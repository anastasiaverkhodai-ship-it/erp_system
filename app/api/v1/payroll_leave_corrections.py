from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.permissions import require_company_permission
from app.models.user import User
from app.models.payroll_leave_correction import PayrollLeaveCorrectionRequest, PayrollLeaveCorrection, PayrollLeaveTransfer
from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication
from app.schemas.payroll_leave_correction import LeaveCorrectionRequestCreate
from app.schemas.payroll_sick_correction_application import SickCorrectionReturnCreate
from app.services.payroll_leave_correction_service import request_leave_correction, apply_leave_correction
from app.services.payroll_sick_return_service import register_sick_return
from app.api.v1.payroll_sick_correction_applications import application_response, ERRORS as FINANCIAL_ERRORS
from app.services.leave_request_service import LeaveRequestError
ERRORS=FINANCIAL_ERRORS+(LeaveRequestError,)

router=APIRouter(prefix='/companies/{company_id}/payroll-leave-corrections',tags=['Payroll leave corrections'])


@router.post('/requests',status_code=201)
async def request(company_id:int,data:LeaveCorrectionRequestCreate,db:AsyncSession=Depends(get_db),
                  actor:User=Depends(require_company_permission('employees.manage'))):
    try:
        row=await request_leave_correction(db,company_id=company_id,actor_id=actor.id,data=data)
        result=await request_response(db,row)
        await db.commit();return result
    except ERRORS as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc
    except Exception:
        await db.rollback();raise


@router.post('/requests/{request_id}/apply',status_code=201)
async def apply(company_id:int,request_id:int,db:AsyncSession=Depends(get_db),
                actor:User=Depends(require_company_permission('employees.manage')),
                accountant:User=Depends(require_company_permission('journal_entries.create'))):
    try:
        row=await apply_leave_correction(db,company_id=company_id,request_id=request_id,actor_id=actor.id)
        result=await leave_application_response(db,row)
        await db.commit();return result
    except ERRORS as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc
    except Exception:
        await db.rollback();raise


@router.post('/returns',status_code=201)
async def returned(company_id:int,data:SickCorrectionReturnCreate,db:AsyncSession=Depends(get_db),
                   actor:User=Depends(require_company_permission('employees.manage')),
                   accountant:User=Depends(require_company_permission('journal_entries.create'))):
    if data.kind!='employee':raise HTTPException(status_code=422,detail='Leave recovery must be an employee return')
    try:
        from app.services.payroll_sick_financial_sources import sick_settlement_chain
        from app.models.payroll_sick_correction_application import PayrollSickPayrollRevision
        chain=await sick_settlement_chain(db,company_id=company_id,calculation_id=data.calculation_id)
        origin=await db.scalar(select(PayrollLeaveCorrection.id).join(PayrollSickPayrollRevision,
            (PayrollSickPayrollRevision.company_id==PayrollLeaveCorrection.company_id)&
            (PayrollSickPayrollRevision.application_id==PayrollLeaveCorrection.application_id)).where(
                PayrollLeaveCorrection.company_id==company_id,
                PayrollSickPayrollRevision.replacement_calculation_id.in_(chain)).limit(1))
        if origin is None:raise ValueError('An applied financial leave correction is required')
        row=await register_sick_return(db,company_id=company_id,actor_id=actor.id,**data.model_dump())
        result=dict(id=row.id,payroll_calculation_id=row.payroll_calculation_id,
            journal_entry_id=row.journal_entry_id,amount=row.amount)
        await db.commit();return result
    except ERRORS as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc
    except Exception:
        await db.rollback();raise


async def leave_application_response(db,application,*,as_of=None):
    result=await application_response(db,application,as_of=as_of)
    evidence=await db.scalar(select(PayrollLeaveCorrection).where(
        PayrollLeaveCorrection.company_id==application.company_id,
        PayrollLeaveCorrection.application_id==application.id))
    if evidence is None:raise ValueError('Leave correction evidence missing')
    transfers=list((await db.scalars(select(PayrollLeaveTransfer.leave_request_id).where(
        PayrollLeaveTransfer.company_id==application.company_id,
        PayrollLeaveTransfer.correction_id==evidence.id).order_by(PayrollLeaveTransfer.id))).all())
    result.update(source_kind='leave',request_id=evidence.request_id,target_kind=evidence.target_kind,
        target_id=evidence.target_id,revision=evidence.revision,reviewed_by=evidence.reviewed_by,
        original_snapshot=evidence.original_snapshot,replacement_snapshot=evidence.replacement_snapshot,
        transferred_leave_request_ids=transfers)
    return result


async def request_response(db,row):
    evidence=await db.scalar(select(PayrollLeaveCorrection).where(
        PayrollLeaveCorrection.company_id==row.company_id,PayrollLeaveCorrection.request_id==row.id))
    return dict(id=row.id,company_id=row.company_id,status='applied' if evidence else 'pending_review',
        created_by=row.created_by,payload=row.payload,application_id=evidence.application_id if evidence else None)


@router.get('/requests/{request_id}')
async def read_request(company_id:int,request_id:int,db:AsyncSession=Depends(get_db),
                       actor:User=Depends(require_company_permission('employees.read'))):
    row=await db.scalar(select(PayrollLeaveCorrectionRequest).where(
        PayrollLeaveCorrectionRequest.company_id==company_id,PayrollLeaveCorrectionRequest.id==request_id))
    if row is None:raise HTTPException(status_code=404,detail='Leave correction request not found')
    return await request_response(db,row)


@router.get('/applications/{application_id}')
async def read_application(company_id:int,application_id:int,as_of:date|None=None,
                           db:AsyncSession=Depends(get_db),
                           actor:User=Depends(require_company_permission('employees.read'))):
    row=await db.scalar(select(PayrollSickCorrectionApplication).where(
        PayrollSickCorrectionApplication.company_id==company_id,
        PayrollSickCorrectionApplication.id==application_id,PayrollSickCorrectionApplication.source_kind=='leave'))
    if row is None:raise HTTPException(status_code=404,detail='Leave correction application not found')
    return await leave_application_response(db,row,as_of=as_of or date.today())
