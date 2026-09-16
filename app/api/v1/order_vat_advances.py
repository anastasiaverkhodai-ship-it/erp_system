"""Company-scoped, transactional advance binding and source correction."""
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.order_vat_advance import OrderVatAdvance, OrderVatAdvanceLine
from app.services.order_vat_advance_service import (
    OrderVatAdvanceError, create_order_vat_advance, reverse_order_vat_advance,
    transfer_order_advances, undo_order_advance_transfer,
)
from app.services.tax_recognition_persistence_service import TaxRecognitionPersistenceError
from app.services.invoice_tax_calculation_service import InvoiceTaxCalculationError
from app.services.payment_settlement_service import PaymentSettlementError
from app.services.tax_recognition_journal_service import TaxRecognitionJournalError

router = APIRouter(prefix='/companies/{company_id}/order-vat-advances', tags=['Order VAT advances'])


class AdvanceCreate(BaseModel):
    order_id: int = Field(gt=0)
    payment_id: int = Field(gt=0)


class TransferCreate(BaseModel):
    invoice_id: int = Field(gt=0)


class AdvanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_id: int
    payment_id: int
    invoice_id: int | None
    status: str
    amount: Decimal
    event_date: date


class AdvanceLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tax_calculation_id: int
    base: Decimal
    tax: Decimal
    gross: Decimal


async def _write(db, operation):
    try:
        rows = await operation
        result = [AdvanceResponse.model_validate(r) for r in rows] if isinstance(rows, list) else AdvanceResponse.model_validate(rows)
        await db.commit()
        return result
    except (OrderVatAdvanceError, TaxRecognitionPersistenceError, InvoiceTaxCalculationError,
            PaymentSettlementError, TaxRecognitionJournalError) as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        await db.rollback()
        raise


@router.post('', response_model=AdvanceResponse)
async def create(company_id: int, payload: AdvanceCreate, db: AsyncSession = Depends(get_db),
                 user=Depends(get_current_user), _=Depends(require_company_permission('payments.settlements.manage'))):
    return await _write(db, create_order_vat_advance(db, company_id=company_id,
        order_id=payload.order_id, payment_id=payload.payment_id, created_by=user.id))


@router.get('', response_model=list[AdvanceResponse])
async def history(company_id: int, after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                  db: AsyncSession = Depends(get_db), _=Depends(require_company_permission('payments.read'))):
    return list((await db.scalars(select(OrderVatAdvance).where(OrderVatAdvance.company_id == company_id,
        OrderVatAdvance.id > after_id).order_by(OrderVatAdvance.id).limit(limit))).all())


@router.get('/{advance_id}/lines', response_model=list[AdvanceLineResponse])
async def lines(company_id: int, advance_id: int, db: AsyncSession = Depends(get_db),
                _=Depends(require_company_permission('payments.read'))):
    return list((await db.scalars(select(OrderVatAdvanceLine).where(OrderVatAdvanceLine.company_id == company_id,
        OrderVatAdvanceLine.advance_id == advance_id).order_by(OrderVatAdvanceLine.id))).all())


@router.post('/{advance_id}/reverse', response_model=AdvanceResponse)
async def reverse(company_id: int, advance_id: int, db: AsyncSession = Depends(get_db),
                  user=Depends(get_current_user), _=Depends(require_company_permission('payments.settlements.manage'))):
    return await _write(db, reverse_order_vat_advance(db, company_id=company_id, advance_id=advance_id, reversed_by=user.id))


@router.post('/orders/{order_id}/transfer', response_model=list[AdvanceResponse])
async def transfer(company_id: int, order_id: int, payload: TransferCreate, db: AsyncSession = Depends(get_db),
                   user=Depends(get_current_user), _=Depends(require_company_permission('payments.settlements.manage'))):
    return await _write(db, transfer_order_advances(db, company_id=company_id, order_id=order_id,
        invoice_id=payload.invoice_id, created_by=user.id))


@router.post('/orders/{order_id}/undo-transfer', response_model=list[AdvanceResponse])
async def undo_transfer(company_id: int, order_id: int, db: AsyncSession = Depends(get_db),
                        user=Depends(get_current_user), _=Depends(require_company_permission('payments.settlements.manage'))):
    return await _write(db, undo_order_advance_transfer(db, company_id=company_id, order_id=order_id, reversed_by=user.id))
