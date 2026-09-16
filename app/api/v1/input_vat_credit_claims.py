from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.input_vat_credit_claim import InputVatCreditClaim
from app.schemas.input_vat_credit_claim import InputVatCreditClaimCreate, InputVatCreditEligibility
from app.services.input_vat_credit_eligibility_service import assess_input_vat_credit
from app.services.input_vat_credit_claim_service import InputVatCreditClaimError, create_input_vat_credit_claim, reverse_input_vat_credit_claim
from app.services.tax_credit_evidence_lifecycle_service import TaxCreditEvidenceLifecycleError
from app.services.tax_credit_evidence_persistence_service import TaxCreditEvidencePersistenceError
from app.services.input_tax_recognition_candidate_loader_service import InputTaxRecognitionCandidateLoaderError
from app.services.input_tax_recognition_calculation_service import InputTaxRecognitionCalculationError

router = APIRouter(prefix='/companies/{company_id}/input-vat-credit', tags=['INPUT VAT credit'])


class ClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    tax_calculation_id: int
    evidence_id: int
    reversal_evidence_id: int | None
    request_key: str
    policy_version: str
    claim_period: date
    attestation: dict
    decision: dict


class ClaimReverse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reversal_date: date


@router.post('/assess', response_model=InputVatCreditEligibility)
async def assess(company_id: int, payload: InputVatCreditClaimCreate,
                 _=Depends(require_company_permission('journal_entries.read'))):
    return assess_input_vat_credit(payload, as_of_date=date.today())


async def _write(db, operation):
    try:
        result = ClaimResponse.model_validate(await operation)
        await db.commit()
        return result
    except (InputVatCreditClaimError, TaxCreditEvidenceLifecycleError, TaxCreditEvidencePersistenceError,
            InputTaxRecognitionCandidateLoaderError, InputTaxRecognitionCalculationError) as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, 'Credit claim conflicts with an existing request or source') from exc
    except Exception:
        await db.rollback()
        raise


@router.post('/claims', response_model=ClaimResponse)
async def create(company_id: int, payload: InputVatCreditClaimCreate, db: AsyncSession = Depends(get_db),
                 user=Depends(get_current_user), _=Depends(require_company_permission('journal_entries.approve'))):
    return await _write(db, create_input_vat_credit_claim(db, company_id=company_id, data=payload, created_by=user.id))


@router.get('/claims', response_model=list[ClaimResponse])
async def history(company_id: int, after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                  db: AsyncSession = Depends(get_db), _=Depends(require_company_permission('journal_entries.read'))):
    return list((await db.scalars(select(InputVatCreditClaim).where(InputVatCreditClaim.company_id == company_id,
        InputVatCreditClaim.id > after_id).order_by(InputVatCreditClaim.id).limit(limit))).all())


@router.post('/claims/{claim_id}/reverse', response_model=ClaimResponse)
async def reverse(company_id: int, claim_id: int, payload: ClaimReverse, db: AsyncSession = Depends(get_db),
                  user=Depends(get_current_user), _=Depends(require_company_permission('journal_entries.approve'))):
    return await _write(db, reverse_input_vat_credit_claim(db, company_id=company_id, claim_id=claim_id,
        reversal_date=payload.reversal_date, reversed_by=user.id))
