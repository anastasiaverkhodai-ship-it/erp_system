from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.permissions import require_company_permission
from app.models.user import User
from app.models.payroll_insurance_funding import PayrollInsuranceFunding
from app.schemas.payroll_insurance_funding import InsuranceFundingCreate, InsuranceFundingRead, InsuranceFundingReverse
from app.services.payroll_insurance_funding_service import (
    register_insurance_funding, funding_record, funding_response, reverse_insurance_funding,
    InsuranceFundingError, InsuranceFundingNotFoundError,
)
from app.services.accounting_reversal import AccountingReversalError
from app.services.accounting_account_role_resolver import AccountingAccountRoleResolutionError
router=APIRouter(prefix='/companies/{company_id}/payroll-insurance-funding',tags=['Payroll insurance funding'])


def http_error(exc):
    return HTTPException(status_code=404 if isinstance(exc,InsuranceFundingNotFoundError) else 409,detail=str(exc))


@router.post('',response_model=InsuranceFundingRead,status_code=201)
async def create(company_id:int,data:InsuranceFundingCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('journal_entries.create')),
    _:User=Depends(require_company_permission('employees.manage'))):
    try:
        row=await register_insurance_funding(db,company_id=company_id,data=data,created_by=actor.id)
        result=await funding_response(db,row)
        await db.commit()
        return result
    except (InsuranceFundingError,AccountingAccountRoleResolutionError) as exc:
        await db.rollback();raise http_error(exc) from exc


@router.get('',response_model=list[InsuranceFundingRead])
async def list_funding(company_id:int,after_id:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500),
    db:AsyncSession=Depends(get_db),actor:User=Depends(require_company_permission('journal_entries.read'))):
    rows=(await db.scalars(select(PayrollInsuranceFunding).where(PayrollInsuranceFunding.company_id==company_id,
        PayrollInsuranceFunding.id>after_id).order_by(PayrollInsuranceFunding.id).limit(limit))).all()
    return [await funding_response(db,row) for row in rows]


@router.get('/{funding_id}',response_model=InsuranceFundingRead)
async def read(company_id:int,funding_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('journal_entries.read'))):
    try:
        return await funding_response(db,await funding_record(db,company_id=company_id,funding_id=funding_id))
    except InsuranceFundingError as exc:
        raise http_error(exc) from exc


@router.post('/{funding_id}/reverse',response_model=InsuranceFundingRead)
async def reverse(company_id:int,funding_id:int,data:InsuranceFundingReverse,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('journal_entries.reverse'))):
    try:
        row=await reverse_insurance_funding(db,company_id=company_id,funding_id=funding_id,
            reversal_date=data.reversal_date,reversed_by=actor.id)
        result=await funding_response(db,row)
        await db.commit()
        return result
    except (InsuranceFundingError,AccountingReversalError) as exc:
        await db.rollback();raise http_error(exc) from exc
