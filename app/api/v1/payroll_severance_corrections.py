"""Reviewed postpayment severance corrections with explicit cash settlement."""
from datetime import date
from decimal import Decimal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.permissions import require_company_permission
from app.models.user import User
from app.models.payroll_severance_correction_case import PayrollSeveranceCorrectionCase as Case
from app.models.payroll import PayrollCalculation
from app.models.payroll_termination_severance import PayrollTerminationSeverance
from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication as Application
from app.api.v1.payroll_sick_correction_applications import application_response,register_return,ERRORS

router=APIRouter(prefix='/companies/{company_id}/payroll-severance-corrections',tags=['Payroll severance corrections'])

class CaseCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    payroll_calculation_id:int=Field(gt=0)
    as_of:date
    evidence_date:date
    evidence_reference:str=Field(min_length=10,max_length=500)
    request_key:str=Field(min_length=8,max_length=120)

class FinancialAmounts(BaseModel):
    model_config=ConfigDict(extra='forbid')
    gross:Decimal=Field(gt=0,max_digits=18,decimal_places=2,allow_inf_nan=False)
    personal_income_tax:Decimal=Field(ge=0,max_digits=18,decimal_places=2,allow_inf_nan=False)
    military_levy:Decimal=Field(ge=0,max_digits=18,decimal_places=2,allow_inf_nan=False)
    employer_contribution:Decimal=Field(ge=0,max_digits=18,decimal_places=2,allow_inf_nan=False)
    net:Decimal=Field(ge=0,max_digits=18,decimal_places=2,allow_inf_nan=False)

class ProposalCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    correction_case_id:int=Field(gt=0)
    proposed_financial:FinancialAmounts
    document_reference:str=Field(min_length=10,max_length=500)
    document_date:date
    request_key:str=Field(min_length=8,max_length=120)

class ReviewCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    review_reference:str=Field(min_length=10,max_length=500)
    review_date:date
    request_key:str=Field(min_length=8,max_length=120)
    approved:bool

class ExecuteCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    correction_date:date

async def transaction(db,operation):
    try:
        result=await operation()
        await db.commit()
        return result
    except ERRORS as exc:
        await db.rollback()
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise

@router.post('/cases',status_code=201)
async def create_case(company_id:int,data:CaseCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    async def operation():
        from app.services.payroll_severance_correction_case_service import open_postpayment_severance_case
        row=await open_postpayment_severance_case(db,company_id=company_id,actor_id=actor.id,**data.model_dump())
        return dict(id=row.id,status=row.status,company_id=company_id,original_calculation_id=row.original_payroll_calculation_id)
    return await transaction(db,operation)

@router.post('/proposals',status_code=201)
async def create_proposal(company_id:int,data:ProposalCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    async def operation():
        case=await db.scalar(select(Case).where(Case.company_id==company_id,Case.id==data.correction_case_id))
        if case is None:raise ValueError('Severance correction case not found')
        calc=await db.get(PayrollCalculation,case.original_payroll_calculation_id)
        source=await db.get(PayrollTerminationSeverance,case.original_severance_source_id)
        from app.services.payroll_severance_calculated_financial import load_calculated_severance_financial
        from app.services.payroll_severance_trusted_classification import TRUSTED_POLICIES
        financial=await load_calculated_severance_financial(db,company_id=company_id,calculation_id=calc.id,
            income_date=source.termination_date,policies=TRUSTED_POLICIES)
        from app.services.payroll_severance_mixed_financial_snapshot import SeveranceMixedFinancialPreview
        from app.services.payroll_severance_net_tax_settlement import SeveranceFinancialSnapshot
        from app.services.payroll_severance_financial_proposal_service import create_severance_financial_proposal
        preview=SeveranceMixedFinancialPreview(company_id=company_id,payroll_calculation_id=calc.id,
            severance_source_ids=(source.id,),ordinary_gross=calc.gross_amount-financial.gross,
            financial=financial,statutory_totals_reconciled=True)
        row=await create_severance_financial_proposal(db,company_id=company_id,correction_case_id=case.id,
            original_preview=preview,proposed_financial=SeveranceFinancialSnapshot(**data.proposed_financial.model_dump()),
            document_reference=data.document_reference,document_date=data.document_date,request_key=data.request_key,submitted_by=actor.id)
        return dict(id=row.id,company_id=company_id,status=row.status,original_financial=row.original_financial,proposed_financial=row.proposed_financial)
    return await transaction(db,operation)

@router.post('/proposals/{proposal_id}/review',status_code=201)
async def review(company_id:int,proposal_id:int,data:ReviewCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('journal_entries.approve'))):
    async def operation():
        from app.services.payroll_severance_independent_review_service import record_independent_severance_review
        row=await record_independent_severance_review(db,company_id=company_id,financial_proposal_id=proposal_id,
            reviewer_id=actor.id,decision='reviewed_pending_statutory' if data.approved else 'rejected',
            **data.model_dump(exclude={'approved'}))
        return dict(id=row.id,decision=row.decision,financial_proposal_id=proposal_id)
    return await transaction(db,operation)

@router.post('/proposals/{proposal_id}/execute',status_code=201)
async def execute(company_id:int,proposal_id:int,data:ExecuteCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage')),
    accountant:User=Depends(require_company_permission('journal_entries.create')),
    reviewer:User=Depends(require_company_permission('journal_entries.approve'))):
    async def operation():
        from app.services.payroll_severance_execution_service import execute_severance_correction
        application=await execute_severance_correction(db,company_id=company_id,proposal_id=proposal_id,
            correction_date=data.correction_date,actor_id=actor.id)
        return await application_response(db,application,as_of=data.correction_date)
    return await transaction(db,operation)

@router.get('/applications/{application_id}')
async def read_application(company_id:int,application_id:int,as_of:date|None=None,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    row=await db.scalar(select(Application).where(Application.company_id==company_id,Application.id==application_id,Application.source_kind=='severance'))
    if row is None:raise HTTPException(status_code=404,detail='Severance correction application not found')
    return await application_response(db,row,as_of=as_of or date.today())

router.add_api_route('/returns',register_return,methods=['POST'],status_code=201)

@router.get('/cases/{case_id}')
async def read_case(company_id:int,case_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    row=await db.scalar(select(Case).where(Case.company_id==company_id,Case.id==case_id))
    if row is None:raise HTTPException(status_code=404,detail='Severance correction case not found')
    return {name:getattr(row,name) for name in (
        'id','company_id','original_payroll_calculation_id','original_severance_source_id',
        'evidence_reference','evidence_date','original_severance_gross',
        'posted_payroll_amount_at_opening','as_of_date','status','created_by','created_at')}

@router.get('/proposals/{proposal_id}')
async def read_proposal(company_id:int,proposal_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    from app.models.payroll_severance_financial_proposal import PayrollSeveranceFinancialProposal as Proposal
    from app.models.payroll_severance_independent_review import PayrollSeveranceIndependentReview as Review
    row=await db.scalar(select(Proposal).where(Proposal.company_id==company_id,Proposal.id==proposal_id))
    if row is None:raise HTTPException(status_code=404,detail='Severance financial proposal not found')
    review=await db.scalar(select(Review).where(Review.company_id==company_id,Review.financial_proposal_id==proposal_id))
    application=await db.scalar(select(Application).where(Application.company_id==company_id,
        Application.source_kind=='severance',Application.request_key==f'severance-proposal:{proposal_id}'))
    result={name:getattr(row,name) for name in (
        'id','company_id','correction_case_id','document_reference','document_date',
        'original_financial','proposed_financial','submitted_by','created_at')}
    result['status']='applied' if application is not None else review.decision if review is not None else row.status
    result['application_id']=application.id if application is not None else None
    result['review']=({name:getattr(review,name) for name in (
        'id','reviewer_id','review_reference','review_date','decision')} if review is not None else None)
    return result
