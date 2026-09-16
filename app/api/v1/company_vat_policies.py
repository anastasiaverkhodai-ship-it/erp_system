from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.company import Company
from app.models.company_vat_policy import CompanyVatPolicy
from app.schemas.company_vat_policy import CompanyVatPolicyCreate, CompanyVatPolicyResponse
from app.services.company_vat_policy_service import append_vat_policy, VatPolicyError
from app.services.ukrainian_vat_rate_catalog import UKRAINIAN_VAT_RATE_CATALOG

router = APIRouter(prefix='/companies/{company_id}/vat-settings', tags=['VAT settings'])


@router.get('')
async def settings(company_id: int, db: AsyncSession = Depends(get_db),
                   user=Depends(require_company_permission('companies.read'))):
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(404, 'Company not found')
    return {'mode': 'strict' if company.vat_policy_enabled else 'legacy_unconfigured',
            'rates': [{'code': r.code, 'rate': str(r.rate), 'treatment': r.treatment,
                       'effective_from': r.effective_from, 'effective_until': r.effective_until}
                      for r in UKRAINIAN_VAT_RATE_CATALOG.all()]}


@router.get('/policies', response_model=list[CompanyVatPolicyResponse])
async def policies(company_id: int, after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                   db: AsyncSession = Depends(get_db), user=Depends(require_company_permission('companies.read'))):
    return list((await db.scalars(select(CompanyVatPolicy).where(
        CompanyVatPolicy.company_id == company_id, CompanyVatPolicy.id > after_id,
    ).order_by(CompanyVatPolicy.id).limit(limit))).all())


@router.post('/policies', response_model=CompanyVatPolicyResponse)
async def create_policy(company_id: int, payload: CompanyVatPolicyCreate, db: AsyncSession = Depends(get_db),
                        user=Depends(require_company_permission('companies.update'))):
    try:
        policy = await append_vat_policy(db, company_id=company_id, data=payload, created_by=user.id)
        response = CompanyVatPolicyResponse.model_validate(policy)
        await db.commit()
        return response
    except VatPolicyError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        await db.rollback()
        raise


from app.models.counterparty_vat_registration import CounterpartyVatRegistration
from app.schemas.company_vat_policy import CounterpartyVatRegistrationCreate, CounterpartyVatRegistrationResponse
from app.services.company_vat_policy_service import append_counterparty_vat_registration


@router.get('/counterparties/{counterparty_id}/registrations', response_model=list[CounterpartyVatRegistrationResponse])
async def counterparty_registrations(company_id: int, counterparty_id: int,
    after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db), user=Depends(require_company_permission('counterparties.read'))):
    return list((await db.scalars(select(CounterpartyVatRegistration).where(
        CounterpartyVatRegistration.company_id == company_id,
        CounterpartyVatRegistration.counterparty_id == counterparty_id,
        CounterpartyVatRegistration.id > after_id,
    ).order_by(CounterpartyVatRegistration.id).limit(limit))).all())


@router.post('/counterparties/{counterparty_id}/registrations', response_model=CounterpartyVatRegistrationResponse)
async def create_registration(company_id: int, counterparty_id: int, payload: CounterpartyVatRegistrationCreate,
    db: AsyncSession = Depends(get_db), user=Depends(require_company_permission('counterparties.update'))):
    try:
        record = await append_counterparty_vat_registration(db, company_id=company_id,
            counterparty_id=counterparty_id, data=payload, created_by=user.id)
        response = CounterpartyVatRegistrationResponse.model_validate(record)
        await db.commit()
        return response
    except VatPolicyError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        await db.rollback()
        raise
