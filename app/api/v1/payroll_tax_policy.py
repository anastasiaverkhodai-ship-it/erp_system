from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.models.payroll_tax import PayrollEmployeeTaxProfile, PayrollStatutoryBaseRule
from app.schemas.payroll_tax_policy import TaxProfileCreate,TaxProfileRead,TaxBaseRuleCreate,TaxBaseRuleRead
from app.services.payroll_tax_policy_service import create_tax_profile,create_tax_base_rule
from app.services.payroll_statutory_service import PayrollStatutoryError

router=APIRouter(prefix='/companies/{company_id}',tags=['Payroll tax policies'])

async def _create(db,operation,schema):
    try:
        row=await operation
        await db.refresh(row)
        result=schema.model_validate(row)
        await db.commit();return result
    except PayrollStatutoryError as exc:
        await db.rollback();raise HTTPException(status_code=409,detail=str(exc)) from exc

@router.post('/payroll-tax-profiles',response_model=TaxProfileRead,status_code=201)
async def post_profile(company_id:int,data:TaxProfileCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    return await _create(db,create_tax_profile(db,company_id=company_id,data=data,created_by=actor.id),TaxProfileRead)

@router.get('/payroll-tax-profiles',response_model=list[TaxProfileRead])
async def get_profiles(company_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    return list((await db.scalars(select(PayrollEmployeeTaxProfile).where(
        PayrollEmployeeTaxProfile.company_id==company_id).order_by(PayrollEmployeeTaxProfile.id))).all())

@router.post('/payroll-statutory-base-rules',response_model=TaxBaseRuleRead,status_code=201)
async def post_rule(company_id:int,data:TaxBaseRuleCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    return await _create(db,create_tax_base_rule(db,company_id=company_id,data=data,created_by=actor.id),TaxBaseRuleRead)

@router.get('/payroll-statutory-base-rules',response_model=list[TaxBaseRuleRead])
async def get_rules(company_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    return list((await db.scalars(select(PayrollStatutoryBaseRule).where(
        PayrollStatutoryBaseRule.company_id==company_id).order_by(PayrollStatutoryBaseRule.id))).all())


from app.schemas.payroll_tax_policy import TaxPolicyEnd
from app.services.payroll_tax_policy_service import end_tax_policy


@router.post('/payroll-tax-profiles/{policy_id}/end', response_model=TaxProfileRead)
async def end_profile(company_id: int, policy_id: int, data: TaxPolicyEnd,
                      db: AsyncSession = Depends(get_db),
                      actor: User = Depends(require_company_permission('employees.manage'))):
    return await _create(db, end_tax_policy(db, company_id=company_id,
        policy_id=policy_id, effective_to=data.effective_to, profile=True), TaxProfileRead)


@router.post('/payroll-statutory-base-rules/{policy_id}/end', response_model=TaxBaseRuleRead)
async def end_rule(company_id: int, policy_id: int, data: TaxPolicyEnd,
                   db: AsyncSession = Depends(get_db),
                   actor: User = Depends(require_company_permission('employees.manage'))):
    return await _create(db, end_tax_policy(db, company_id=company_id,
        policy_id=policy_id, effective_to=data.effective_to, profile=False), TaxBaseRuleRead)
