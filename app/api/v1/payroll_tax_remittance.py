from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.permissions import require_company_permission
from app.models.user import User
from app.models.payroll_tax_remittance import PayrollTaxRemittance
from app.schemas.payroll_tax_remittance import TaxRemittanceCreate, TaxRemittanceRead, TaxRemittanceReverse, TaxBalanceRead
from app.services.payroll_tax_remittance_service import (
    register_tax_remittance, remittance_record, remittance_response, reverse_tax_remittance,
    TaxRemittanceError, TaxRemittanceNotFoundError,
)
from app.services.accounting_reversal import AccountingReversalError
from app.services.accounting_account_role_resolver import AccountingAccountRoleResolutionError
router=APIRouter(prefix='/companies/{company_id}/payroll-tax-remittances',tags=['Payroll tax remittances'])


def http_error(exc):
    return HTTPException(status_code=404 if isinstance(exc,TaxRemittanceNotFoundError) else 409,detail=str(exc))


@router.post('',response_model=TaxRemittanceRead,status_code=201)
async def create(company_id:int,data:TaxRemittanceCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('journal_entries.create')),
    _:User=Depends(require_company_permission('employees.manage'))):
    try:
        row=await register_tax_remittance(db,company_id=company_id,data=data,created_by=actor.id)
        result=await remittance_response(db,row)
        await db.commit()
        return result
    except (TaxRemittanceError,AccountingAccountRoleResolutionError) as exc:
        await db.rollback();raise http_error(exc) from exc


@router.get('',response_model=list[TaxRemittanceRead])
async def list_funding(company_id:int,after_id:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500),
    db:AsyncSession=Depends(get_db),actor:User=Depends(require_company_permission('journal_entries.read'))):
    rows=(await db.scalars(select(PayrollTaxRemittance).where(PayrollTaxRemittance.company_id==company_id,
        PayrollTaxRemittance.id>after_id).order_by(PayrollTaxRemittance.id).limit(limit))).all()
    return [await remittance_response(db,row) for row in rows]


@router.get('/balances',response_model=TaxBalanceRead)
async def balances(company_id:int,as_of:date,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('journal_entries.read'))):
    from app.services.payroll_tax_balance_service import payroll_tax_balances
    try:
        return await payroll_tax_balances(db,company_id=company_id,as_of=as_of)
    except TaxRemittanceError as exc:
        raise http_error(exc) from exc


@router.get('/{remittance_id}',response_model=TaxRemittanceRead)
async def read(company_id:int,remittance_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('journal_entries.read'))):
    try:
        return await remittance_response(db,await remittance_record(db,company_id=company_id,remittance_id=remittance_id))
    except TaxRemittanceError as exc:
        raise http_error(exc) from exc


@router.post('/{remittance_id}/reverse',response_model=TaxRemittanceRead)
async def reverse(company_id:int,remittance_id:int,data:TaxRemittanceReverse,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('journal_entries.reverse'))):
    try:
        row=await reverse_tax_remittance(db,company_id=company_id,remittance_id=remittance_id,
            reversal_date=data.reversal_date,reversed_by=actor.id)
        result=await remittance_response(db,row)
        await db.commit()
        return result
    except (TaxRemittanceError,AccountingReversalError) as exc:
        await db.rollback();raise http_error(exc) from exc
