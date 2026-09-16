from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.purchase_quote import PurchaseQuote
from app.schemas.purchase_quote import PurchaseQuoteCreate, PurchaseQuoteResponse, PurchaseQuoteComparisonRequest, PurchaseQuoteComparisonResponse
from app.services.purchase_quote_service import PurchaseQuoteError, create_purchase_quote, withdraw_purchase_quote, compare_purchase_quotes

router = APIRouter(prefix="/companies/{company_id}/purchase-quotes", tags=["Purchase Quotes"])


@router.post("", response_model=PurchaseQuoteResponse)
async def create_quote(company_id: int, payload: PurchaseQuoteCreate, db: AsyncSession = Depends(get_db),
                       user=Depends(get_current_user), _=Depends(require_company_permission("trade_documents.create"))):
    try:
        quote = await create_purchase_quote(db, company_id=company_id, data=payload, created_by=user.id)
        response = PurchaseQuoteResponse.model_validate(quote)
        await db.commit()
        return response
    except PurchaseQuoteError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        await db.rollback()
        raise


@router.get("", response_model=list[PurchaseQuoteResponse])
async def list_quotes(company_id: int, product_id: int | None = Query(default=None, gt=0),
                      limit: int = Query(default=100, ge=1, le=500), after_id: int = Query(default=0, ge=0),
                      db: AsyncSession = Depends(get_db), _=Depends(require_company_permission("trade_documents.read"))):
    statement = select(PurchaseQuote).where(PurchaseQuote.company_id == company_id, PurchaseQuote.id > after_id)
    if product_id is not None:
        statement = statement.where(PurchaseQuote.product_id == product_id)
    return list((await db.scalars(statement.order_by(PurchaseQuote.id).limit(limit))).all())


@router.post("/compare", response_model=PurchaseQuoteComparisonResponse)
async def compare_quotes(company_id: int, payload: PurchaseQuoteComparisonRequest, db: AsyncSession = Depends(get_db),
                         _=Depends(require_company_permission("trade_documents.read"))):
    try:
        return await compare_purchase_quotes(db, company_id=company_id, request=payload)
    except PurchaseQuoteError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{quote_id}/withdraw", response_model=PurchaseQuoteResponse)
async def withdraw_quote(company_id: int, quote_id: int, db: AsyncSession = Depends(get_db),
                         user=Depends(get_current_user), _=Depends(require_company_permission("trade_documents.update"))):
    try:
        quote = await withdraw_purchase_quote(db, company_id=company_id, quote_id=quote_id, withdrawn_by=user.id)
        response = PurchaseQuoteResponse.model_validate(quote)
        await db.commit()
        return response
    except PurchaseQuoteError as exc:
        await db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except Exception:
        await db.rollback()
        raise
