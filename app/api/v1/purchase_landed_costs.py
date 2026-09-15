"""Capitalize already posted expenses with company-scoped accounting permissions."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.purchase_landed_cost_event import PurchaseLandedCostEvent
from app.schemas.purchase_landed_cost import (
    PurchaseLandedCostCreate, PurchaseLandedCostReverse, PurchaseLandedCostResponse,
)
from app.services.accounting_posting import AccountingPostingError
from app.services.accounting_reversal import AccountingReversalError
from app.services.purchase_landed_cost_capitalization_service import (
    capitalize_purchase_landed_cost, reverse_capitalized_purchase_landed_cost,
)
from app.services.purchase_landed_cost_flow_service import PurchaseLandedCostFlowError
from app.services.purchase_landed_cost_allocation_calculation_service import PurchaseLandedCostAllocationCalculationError
from app.services.purchase_landed_cost_persistence_service import PurchaseLandedCostPersistenceError

router = APIRouter(prefix="/companies/{company_id}/purchase-landed-costs", tags=["Purchase Landed Costs"])
BUSINESS_ERRORS = (PurchaseLandedCostPersistenceError, PurchaseLandedCostFlowError, PurchaseLandedCostAllocationCalculationError,
                   AccountingPostingError, AccountingReversalError)


@router.get("", response_model=list[PurchaseLandedCostResponse])
async def list_landed_costs(company_id: int, db: AsyncSession = Depends(get_db),
                           _=Depends(require_company_permission("journal_entries.read"))):
    return list((await db.execute(select(PurchaseLandedCostEvent).where(
        PurchaseLandedCostEvent.company_id == company_id,
    ).order_by(PurchaseLandedCostEvent.id))).scalars().all())


@router.post("", response_model=PurchaseLandedCostResponse)
async def create_landed_cost(company_id: int, payload: PurchaseLandedCostCreate,
                            db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user),
                            _=Depends(require_company_permission("journal_entries.post"))):
    try:
        result = await capitalize_purchase_landed_cost(
            db, company_id=company_id, created_by=current_user.id, **payload.model_dump(),
        )
        response = PurchaseLandedCostResponse.model_validate(result.event)
        await db.commit()
        return response
    except BUSINESS_ERRORS as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise


@router.post("/{event_id}/reverse", response_model=PurchaseLandedCostResponse)
async def reverse_landed_cost(company_id: int, event_id: int, payload: PurchaseLandedCostReverse,
                             db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user),
                             _=Depends(require_company_permission("journal_entries.reverse"))):
    try:
        result = await reverse_capitalized_purchase_landed_cost(
            db, company_id=company_id, landed_cost_event_id=event_id,
            reversed_by=current_user.id, **payload.model_dump(),
        )
        response = PurchaseLandedCostResponse.model_validate(result.event)
        await db.commit()
        return response
    except BUSINESS_ERRORS as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise
