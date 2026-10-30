from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.permissions import require_company_permission
from app.models.user import User
from app.models.payroll_income_dates import PayrollIncomeDateRequest as Request, PayrollIncomeDateApproval as Approval
from app.schemas.payroll_income_dates import IncomeDateRequestCreate
from app.services.payroll_income_dates_service import request_income_dates,approve_income_dates

router=APIRouter(prefix='/companies/{company_id}/payroll-income-dates',tags=['Payroll income dates'])

@router.post('/requests',status_code=201)
async def create(company_id:int,data:IncomeDateRequestCreate,db:AsyncSession=Depends(get_db),
                 actor:User=Depends(require_company_permission('employees.manage'))):
    try:
        row=await request_income_dates(db,company_id=company_id,actor_id=actor.id,data=data)
        result=dict(id=row.id,line_id=row.line_id,payload=row.payload)
        await db.commit();return result
    except ValueError as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc
    except Exception:
        await db.rollback();raise

@router.post('/requests/{request_id}/approve',status_code=201)
async def approve(company_id:int,request_id:int,db:AsyncSession=Depends(get_db),
                  actor:User=Depends(require_company_permission('employees.manage')),
                  accountant:User=Depends(require_company_permission('journal_entries.create'))):
    try:
        row=await approve_income_dates(db,company_id=company_id,request_id=request_id,actor_id=actor.id)
        result=dict(id=row.id,request_id=row.request_id,line_id=row.line_id,status='approved')
        await db.commit();return result
    except ValueError as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc
    except Exception:
        await db.rollback();raise

@router.get('/requests/{request_id}')
async def read(company_id:int,request_id:int,db:AsyncSession=Depends(get_db),
               actor:User=Depends(require_company_permission('employees.read'))):
    row=await db.scalar(select(Request).where(Request.company_id==company_id,Request.id==request_id))
    if row is None:raise HTTPException(status_code=404,detail='Income-date request not found')
    approval=await db.scalar(select(Approval).where(Approval.company_id==company_id,Approval.request_id==row.id))
    return dict(id=row.id,line_id=row.line_id,payload=row.payload,status='approved' if approval else 'pending_review')
