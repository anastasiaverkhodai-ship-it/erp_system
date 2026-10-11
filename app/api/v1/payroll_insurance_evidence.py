from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.payroll_insurance_evidence import InsuranceEvidenceCreate, InsuranceEvidenceRead, InsuranceEvidenceReview
from app.services.payroll_insurance_evidence_service import (
    create_insurance_evidence, get_insurance_evidence, review_insurance_evidence,
    PayrollInsuranceEvidenceError, PayrollInsuranceEvidenceNotFoundError,
)
router = APIRouter(prefix='/companies/{company_id}/payroll-insurance-evidence',tags=['Payroll insurance evidence'])


def error(exc):
    return HTTPException(status_code=404 if isinstance(exc,PayrollInsuranceEvidenceNotFoundError) else 409,detail=str(exc))


@router.post('',response_model=InsuranceEvidenceRead,status_code=201)
async def create(company_id:int,data:InsuranceEvidenceCreate,db:AsyncSession=Depends(get_db),
                 actor:User=Depends(require_company_permission('employees.manage'))):
    try:
        row=await create_insurance_evidence(db,company_id=company_id,data=data,created_by=actor.id)
        await db.commit();await db.refresh(row)
        return row
    except PayrollInsuranceEvidenceError as exc:
        await db.rollback();raise error(exc) from exc


@router.get('/{evidence_id}',response_model=InsuranceEvidenceRead)
async def read(company_id:int,evidence_id:int,db:AsyncSession=Depends(get_db),
               actor:User=Depends(require_company_permission('employees.read'))):
    try:
        return await get_insurance_evidence(db,company_id=company_id,evidence_id=evidence_id)
    except PayrollInsuranceEvidenceError as exc:
        raise error(exc) from exc


@router.post('/{evidence_id}/review',response_model=InsuranceEvidenceRead)
async def review(company_id:int,evidence_id:int,data:InsuranceEvidenceReview,db:AsyncSession=Depends(get_db),
                 actor:User=Depends(require_company_permission('employees.manage'))):
    try:
        row=await review_insurance_evidence(db,company_id=company_id,evidence_id=evidence_id,
            reviewed_by=actor.id,approved=data.approved)
        await db.commit();await db.refresh(row)
        return row
    except PayrollInsuranceEvidenceError as exc:
        await db.rollback();raise error(exc) from exc
