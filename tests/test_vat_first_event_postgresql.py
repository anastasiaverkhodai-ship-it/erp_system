"""Persistent sources -> recognition history -> GL; private rollback-only schema."""
from datetime import date, datetime, timezone
from decimal import Decimal as D
import os
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.services.tax_recognition_persistence_service import TaxRecognitionDataIntegrityError
from app.services.input_tax_recognition_candidate_loader_service import InputTaxRecognitionCandidateLoaderIntegrityError
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool
import app.models
from app.core.config import settings
from app.core.database import Base
from app.models.company import Company
from app.models.company_vat_policy import CompanyVatPolicy
from app.models.counterparty_vat_registration import CounterpartyVatRegistration
from app.models.user import User
from app.models.counterparty import Counterparty
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine
from app.models.document import Document
from app.models.document_line import DocumentLine
from app.models.trade_fulfillment import TradeFulfillment
from app.models.trade_fulfillment_line import TradeFulfillmentLine
from app.models.counterparty_open_item import CounterpartyOpenItem
from app.models.payment import Payment
from app.models.payment_settlement_allocation import PaymentSettlementAllocation
from app.models.invoice_fulfillment_allocation import InvoiceFulfillmentAllocation
from app.models.tax_calculation import TaxCalculation
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.models.accounting_period import AccountingPeriod
from app.models.journal_entry import JournalEntry
from app.services.company_chart_of_accounts_seeding_service import seed_company_chart_of_accounts
from app.services.tax_credit_evidence_persistence_service import create_tax_credit_evidence
from app.services.tax_credit_evidence_types import TaxCreditEvidenceType
from app.services.tax_recognition_reconciliation_service import reconcile_output_tax_calculation_from_active_sources
from app.services.input_tax_recognition_reconciliation_service import reconcile_input_tax_calculation_from_active_sources
from app.services.tax_recognition_lifecycle_service import _post_created_output_vat_recognition_events, _post_created_input_vat_recognition_events

pytestmark=pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')
D1,D2,D3,D4=(date(2026,9,i) for i in (1,2,3,4))


async def seed(db, purchase, payment_first):
    direction='purchase' if purchase else 'sale'; warehouse_type='receipt' if purchase else 'issue'
    db.add_all([Company(id=1,name='First event'),User(id=1,email='vat-first@example.test',password_hash='unused',first_name='Test',last_name='User')])
    await db.flush()
    db.add_all([Counterparty(id=1,company_id=1,name='Party',counterparty_type='both'),
        Product(id=1,company_id=1,name='Goods',sku='FIRST'),Warehouse(id=1,company_id=1,name='Warehouse'),
        AccountingPeriod(company_id=1,year=2026,month=9,start_date=D1,end_date=date(2026,9,30),status='open')])
    await db.flush()
    await seed_company_chart_of_accounts(session=db,company_id=1)
    # Payment predates invoice creation; allocation comes later, but retains payment_date.
    payment=Payment(id=1,company_id=1,counterparty_id=1,number='P',direction='outgoing' if purchase else 'incoming',
        status='confirmed',confirmed_at=datetime(2026,9,1,tzinfo=timezone.utc),payment_date=D1 if payment_first else D2,
        currency_code='UAH',amount=D(72),created_by=1)
    db.add(payment); await db.flush()
    db.add_all([TradeDocument(id=1,company_id=1,counterparty_id=1,number='ORDER',direction=direction,kind='order',status='fulfilled',document_date=D1,created_by=1),
        TradeDocument(id=2,company_id=1,counterparty_id=1,number='INVOICE',direction=direction,kind='invoice',status='confirmed',document_date=D3,created_by=1)])
    await db.flush()
    db.add_all([TradeDocumentLine(id=i,company_id=1,trade_document_id=i,line_number=1,product_id=1,warehouse_id=1,
        quantity=D(10),unit_price=D(10),tax_rate_code='VAT20',tax_recognition_method='first_event',tax_price_mode='exclusive') for i in (1,2)])
    db.add(Document(id=1,company_id=1,number='WAREHOUSE',document_type=warehouse_type,status='posted',document_date=D2 if payment_first else D1,created_by=1))
    db.add(CounterpartyOpenItem(id=1,company_id=1,trade_document_id=2,counterparty_id=1,item_type='payable' if purchase else 'receivable',
        status='open',document_date=D3,due_date=D3,currency_code='UAH',original_amount=D(120)))
    await db.flush()
    db.add(DocumentLine(id=1,document_id=1,product_id=1,warehouse_id=1,quantity=D(7),price=D(10)))
    db.add(TradeFulfillment(id=1,company_id=1,trade_document_id=1,warehouse_document_id=1,warehouse_document_type=warehouse_type,created_by=1))
    await db.flush()
    db.add(TradeFulfillmentLine(id=1,company_id=1,fulfillment_id=1,trade_document_id=1,trade_document_line_id=1,
        warehouse_document_id=1,warehouse_document_line_id=1,product_id=1,warehouse_id=1,quantity=D(7)))
    db.add(TaxCalculation(id=1,company_id=1,trade_document_id=2,trade_document_line_id=2,product_id=1,tax_type='vat',direction='input' if purchase else 'output',
        tax_rate_code='VAT20',tax_rate=D('.20'),treatment='taxable',recognition_method='first_event',taxable_base=D(100),tax_amount=D(20),currency_code='UAH',calculation_date=D3))
    await db.flush()
    db.add(CompanyVatPolicy(id=1,company_id=1,effective_from=D1,payer_status='vat_payer',vat_number='111',legal_basis='Extract',created_by=1))
    db.add(CompanyVatPolicy(id=2,company_id=1,effective_from=D3,payer_status='vat_payer',vat_number='111',legal_basis='Updated extract',created_by=1))
    db.add(CounterpartyVatRegistration(id=1,company_id=1,counterparty_id=1,effective_from=D1,payer_status='vat_payer',vat_number='222',legal_basis='Extract',created_by=1))
    await db.flush()
    invoice=await db.get(TradeDocument,2)
    invoice.vat_policy_id=2; invoice.counterparty_vat_registration_id=1
    await db.flush()
    if purchase:
        await create_tax_credit_evidence(db,company_id=1,tax_calculation_id=1,evidence_type=TaxCreditEvidenceType.REGISTERED_TAX_INVOICE,
            evidence_number='E1',evidence_date=D1,credit_available_date=D1,evidenced_taxable_base=D(100),evidenced_tax_amount=D(20),currency_code='UAH',created_by=1)
    payment_alloc=PaymentSettlementAllocation(id=1,company_id=1,payment_id=1,open_item_id=1,amount=D(72),created_by=1)
    fulfillment_alloc=InvoiceFulfillmentAllocation(id=1,company_id=1,invoice_id=2,invoice_line_id=2,fulfillment_id=1,
        fulfillment_line_id=1,order_id=1,order_line_id=1,product_id=1,quantity=D(7),created_by=1)
    return payment_alloc,fulfillment_alloc


@pytest.mark.asyncio
@pytest.mark.parametrize('purchase',[False,True])
@pytest.mark.parametrize('payment_first',[False,True])
async def test_partial_first_event_persistence_gl_reversal_and_replay(purchase,payment_first):
    engine=create_async_engine(settings.database_url,poolclass=NullPool)
    schema='test_first_event_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                async with AsyncSession(conn,expire_on_commit=False) as db:
                    payment,fulfillment=await seed(db,purchase,payment_first)
                    reconcile=reconcile_input_tax_calculation_from_active_sources if purchase else reconcile_output_tax_calculation_from_active_sources
                    post=_post_created_input_vat_recognition_events if purchase else _post_created_output_vat_recognition_events
                    async def run(day):
                        result=await reconcile(db,company_id=1,tax_calculation_id=1,adjustment_date=day,created_by=1)
                        await post(db,result=result,created_by=1)
                        await db.flush()
                        from app.services.output_vat_gl_reconciliation_service import reconcile_output_vat_gl
                        control = await reconcile_output_vat_gl(db, company_id=1, date_from=D1, date_to=D4)
                        assert control.matched, control.issues
                        if not purchase:
                            assert control.expected_output_vat == await net()
                        else:
                            assert control.event_count == 0  # INPUT is excluded from OUTPUT control.
                        return result
                    async def net():
                        events=list((await db.scalars(select(TaxRecognitionEvent))).all())
                        return sum((e.recognized_tax_amount if e.reversal_of_id is None else -e.recognized_tax_amount for e in events),D(0))
                    first,second=(payment,fulfillment) if payment_first else (fulfillment,payment)
                    db.add(first); await db.flush()
                    # First-event registration, not later invoice status, must govern.
                    with pytest.raises((TaxRecognitionDataIntegrityError, InputTaxRecognitionCandidateLoaderIntegrityError), match='differs'):
                        async with db.begin_nested():
                            await db.execute(text("UPDATE company_vat_policies SET payer_status='non_vat_payer', vat_number=NULL WHERE id=1"))
                            await run(D1)
                    assert await db.scalar(select(func.count()).select_from(TaxRecognitionEvent)) == 0
                    await run(D1)
                    assert await net()==(D(12) if payment_first else D(14))
                    db.add(second); await db.flush()
                    await run(D2)
                    assert await net()==D(14)
                    repeat=await run(D3)
                    assert repeat.created_events == ()
                    journals=await db.scalar(select(func.count()).select_from(JournalEntry))
                    assert journals>0
                    # Closed-period failure rolls back recognition AND GL, including source reversal.
                    with pytest.raises(HTTPException, match='closed'):
                        async with db.begin_nested():
                            await db.execute(text("UPDATE accounting_periods SET status='closed', is_locked=true"))
                            fulfillment.status='reversed'; fulfillment.reversed_by=1
                            fulfillment.reversed_at=datetime(2026,9,4,tzinfo=timezone.utc)
                            await db.flush()
                            await run(D4)
                    await db.refresh(fulfillment)
                    assert fulfillment.status == 'active'
                    assert await net() == D(14)
                    assert await db.scalar(select(func.count()).select_from(JournalEntry)) == journals
                    # Removing the supply leaves only the advance; history is reversed, not overwritten.
                    fulfillment.status='reversed'; fulfillment.reversed_by=1
                    fulfillment.reversed_at=datetime(2026,9,4,tzinfo=timezone.utc)
                    await db.flush()
                    await run(D4)
                    assert await net()==D(12)
                    assert (await run(D4)).created_events == ()
                    # Every generated journal remains balanced.
                    unbalanced=await db.scalar(text('''SELECT count(*) FROM (SELECT journal_entry_id FROM journal_entry_lines
                        GROUP BY journal_entry_id HAVING sum(debit) != sum(credit)) t'''))
                    assert unbalanced==0
            finally:
                await tx.rollback()
            assert await conn.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=:s'),{'s':schema})==0
    finally:
        await engine.dispose()
