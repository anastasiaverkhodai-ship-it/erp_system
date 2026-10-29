from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.permissions import require_company_permission
from app.models.user import User
from app.models.payroll_attendance_correction import PayrollAttendanceCorrectionRequest as Request, PayrollAttendanceCorrection as Evidence
from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication as Application
from app.schemas.payroll_attendance_correction import AttendanceCorrectionCreate
from app.services.payroll_attendance_correction_service import request_attendance_correction, apply_attendance_correction, effective_attendance_changes
from app.api.v1.payroll_sick_correction_applications import application_response, ERRORS

router=APIRouter(prefix='/companies/{company_id}/payroll-attendance-corrections', tags=['Payroll attendance corrections'])


async def request_response(db, row):
    evidence=await db.scalar(select(Evidence).where(Evidence.company_id==row.company_id, Evidence.request_id==row.id))
    return dict(id=row.id, company_id=row.company_id, payload=row.payload,
        created_by=row.created_by, status='applied' if evidence else 'pending_review',
        application_id=evidence.application_id if evidence else None)


@router.post('/requests',status_code=201)
async def request(company_id:int,data:AttendanceCorrectionCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    try:
        row=await request_attendance_correction(db,company_id=company_id,actor_id=actor.id,data=data)
        result=await request_response(db,row)
        await db.commit();return result
    except ERRORS as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc
    except Exception:
        await db.rollback();raise


@router.get('/requests/{request_id}')
async def read_request(company_id:int,request_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    row=await db.scalar(select(Request).where(Request.company_id==company_id,Request.id==request_id))
    if row is None:raise HTTPException(status_code=404,detail='Attendance correction request not found')
    return await request_response(db,row)


@router.post('/requests/{request_id}/apply',status_code=201)
async def apply(company_id:int,request_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage')),
    accountant:User=Depends(require_company_permission('journal_entries.create'))):
    try:
        row=await apply_attendance_correction(db,company_id=company_id,request_id=request_id,actor_id=actor.id)
        result=await application_response(db,row,as_of=row.correction_date)
        await db.commit();return result
    except ERRORS as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc
    except Exception:
        await db.rollback();raise


@router.get('/applications/{application_id}')
async def read_application(company_id:int,application_id:int,as_of:date|None=None,
    db:AsyncSession=Depends(get_db),actor:User=Depends(require_company_permission('employees.read'))):
    row=await db.scalar(select(Application).where(Application.company_id==company_id,
        Application.id==application_id,Application.source_kind=='attendance'))
    if row is None:raise HTTPException(status_code=404,detail='Attendance correction application not found')
    return await application_response(db,row,as_of=as_of or date.today())


@router.get('/contracts/{contract_id}/days')
async def effective_days(company_id:int,contract_id:int,as_of:date|None=None,
    db:AsyncSession=Depends(get_db),actor:User=Depends(require_company_permission('employees.read'))):
    return list((await effective_attendance_changes(db,company_id=company_id,contract_id=contract_id,as_of=as_of)).values())


# All financial corrections share the same real-bank-return ledger, including
# attribution, idempotency, overpayment limits and company permissions.
from app.api.v1.payroll_sick_correction_applications import register_return
router.add_api_route('/returns', register_return, methods=['POST'], status_code=201)
