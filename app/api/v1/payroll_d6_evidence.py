"""Review special-service facts independently of official XML generation."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.permissions import require_company_permission
from app.models.user import User
from app.schemas.payroll_d6_evidence import D6EvidenceCreate, D6EvidenceReviewCreate
from app.services.payroll_d6_evidence_service import create_evidence, review_evidence, period_evidence, get_evidence, D6MigrationRequired

router=APIRouter(prefix="/companies/{company_id}", tags=["Payroll regulatory reporting"])


@router.post("/payroll-d6-evidence", status_code=201)
async def create(company_id:int, data:D6EvidenceCreate, db:AsyncSession=Depends(get_db),
                 actor:User=Depends(require_company_permission("employees.manage"))):
    try:
        item=await create_evidence(db,company_id=company_id,actor_id=actor.id,data=data)
        result={"id":item.id,"revision":item.revision,"fingerprint":item.fingerprint,
            "official_statutory_generation":"BLOCKED"}
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=503 if isinstance(exc,D6MigrationRequired) else 400,detail=str(exc)) from exc


@router.post("/payroll-d6-evidence/{evidence_id}/review")
async def review(company_id:int,evidence_id:int,data:D6EvidenceReviewCreate,db:AsyncSession=Depends(get_db),
                 actor:User=Depends(require_company_permission("employees.manage"))):
    try:
        item=await review_evidence(db,company_id=company_id,actor_id=actor.id,evidence_id=evidence_id,data=data)
        result={"id":item.id,"evidence_id":item.evidence_id,"decision":item.decision,
            "official_statutory_generation":"BLOCKED"}
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=503 if isinstance(exc,D6MigrationRequired) else 400,detail=str(exc)) from exc


@router.get("/payroll-periods/{payroll_period_id}/d6-evidence")
async def summary(company_id:int,payroll_period_id:int,db:AsyncSession=Depends(get_db),
                  actor:User=Depends(require_company_permission("employees.read"))):
    try:
        return await period_evidence(db,company_id=company_id,payroll_period_id=payroll_period_id)
    except ValueError as exc:
        raise HTTPException(status_code=503 if isinstance(exc,D6MigrationRequired) else 404,detail=str(exc)) from exc


@router.get("/payroll-d6-evidence/{evidence_id}")
async def read(company_id:int,evidence_id:int,db:AsyncSession=Depends(get_db),
               actor:User=Depends(require_company_permission("employees.read"))):
    try:
        return await get_evidence(db,company_id=company_id,evidence_id=evidence_id)
    except ValueError as exc:
        raise HTTPException(status_code=503 if isinstance(exc,D6MigrationRequired) else 404,detail=str(exc)) from exc
