import os
from datetime import date
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import select,text,func
from sqlalchemy.ext.asyncio import create_async_engine,AsyncSession
from sqlalchemy.pool import NullPool
import app.models
from app.core.database import Base
from app.core.config import settings
from app.models.company_vat_policy import CompanyVatPolicy
from app.models.counterparty_vat_registration import CounterpartyVatRegistration
from app.models.tax_calculation import TaxCalculation
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.models.input_vat_credit_claim import InputVatCreditClaim
from app.services.input_vat_credit_claim_service import create_input_vat_credit_claim,reverse_input_vat_credit_claim
from app.services.order_vat_advance_service import create_order_vat_advance,transfer_order_advances,reverse_order_vat_advance,OrderVatAdvanceError
from test_order_vat_advance_postgresql import seed,invoice,DAY
from test_input_vat_credit_eligibility import payload

pytestmark=pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')


@pytest.mark.asyncio
async def test_order_credit_handoff_preserves_net_credit_and_replays_atomically():
    engine=create_async_engine(settings.database_url,poolclass=NullPool)
    schema='test_order_credit_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                async with AsyncSession(conn,expire_on_commit=False) as db:
                    order=await seed(db,True)
                    db.add(CompanyVatPolicy(id=1,company_id=1,effective_from=DAY,payer_status='vat_payer',vat_number='111',legal_basis='Extract',created_by=1))
                    db.add(CounterpartyVatRegistration(id=1,company_id=1,counterparty_id=1,effective_from=DAY,payer_status='vat_payer',vat_number='222',legal_basis='Extract',created_by=1))
                    await db.flush()
                    order.vat_policy_id=1;order.counterparty_vat_registration_id=1
                    await db.flush()
                    advance=await create_order_vat_advance(db,company_id=1,order_id=order.id,payment_id=1,created_by=1)
                    calc=await db.scalar(select(TaxCalculation).where(TaxCalculation.trade_document_id==order.id))
                    data=payload(tax_calculation_id=calc.id,invoice_date=DAY,registered_on=DAY,claim_period=DAY)
                    claim=await create_input_vat_credit_claim(db,company_id=1,data=data,created_by=1)
                    async def net():
                        return await db.scalar(text('SELECT coalesce(sum(CASE WHEN reversal_of_id IS NULL THEN recognized_tax_amount ELSE -recognized_tax_amount END),0) FROM tax_recognition_events'))
                    assert await net()==Decimal(12)
                    assert (await create_input_vat_credit_claim(db,company_id=1,data=data,created_by=1)).id==claim.id
                    with pytest.raises(OrderVatAdvanceError,match='Reverse active'):
                        await reverse_order_vat_advance(db,company_id=1,advance_id=advance.id,reversed_by=1)
                    await db.refresh(order,['lines'])
                    inv=await invoice(db,order)
                    inv.vat_policy_id=1;inv.counterparty_vat_registration_id=1
                    await db.flush()
                    await transfer_order_advances(db,company_id=1,order_id=order.id,invoice_id=inv.id,created_by=1)
                    assert await net()==Decimal(12)
                    claims=list((await db.scalars(select(InputVatCreditClaim).order_by(InputVatCreditClaim.id))).all())
                    assert len(claims)==2 and claims[0].reversal_evidence_id is not None
                    assert claims[1].decision['transfer_from_claim_id']==claim.id
                    count=await db.scalar(select(func.count()).select_from(TaxRecognitionEvent))
                    await transfer_order_advances(db,company_id=1,order_id=order.id,invoice_id=inv.id,created_by=1)
                    assert await db.scalar(select(func.count()).select_from(TaxRecognitionEvent))==count
                    await reverse_input_vat_credit_claim(db,company_id=1,claim_id=claims[1].id,reversal_date=date(2026,9,10),reversed_by=1)
                    assert await net()==0
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
