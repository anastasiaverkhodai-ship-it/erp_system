import importlib.util
import os
from pathlib import Path
from datetime import date,datetime,timezone
from decimal import Decimal as D
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select,text,func
from sqlalchemy.ext.asyncio import create_async_engine,AsyncSession
from sqlalchemy.pool import NullPool
from alembic.migration import MigrationContext
from alembic.operations import Operations
import app.models
from app.core.database import Base
from app.core.config import settings
from app.models.product_tax_classification import ProductTaxClassification
from app.models.trade_value_correction_event import TradeValueCorrectionEvent
from app.models.journal_entry import JournalEntry
from app.models.tax_invoice_line import TaxInvoiceLine
from app.models.tax_invoice_correction_line import TaxInvoiceCorrectionLine
from app.services.tax_recognition_reconciliation_service import reconcile_output_tax_calculation_from_active_sources
from app.services.tax_recognition_lifecycle_service import _post_created_output_vat_recognition_events
from app.services.tax_invoice_output_persistence_service import create_output_tax_invoice
from app.services.tax_invoice_registration_lifecycle_service import append_tax_invoice_registration_event
from app.services.tax_invoice_correction_orchestration_service import create_tax_invoice_correction_idempotent,append_tax_invoice_correction_registration_idempotent
from app.services.tax_invoice_correction_source_resolver_service import TaxInvoiceCorrectionLineSource
from app.services.vat_register_service import get_vat_register
from app.services.tax_invoice_correction_accounting_service import load_posted_output_corrections
from app.services.accounting_reversal import reverse_journal_entry,AccountingReversalError
from test_vat_first_event_postgresql import seed,D1

pytestmark=pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')
DAY=date(2026,9,5)


@pytest.mark.asyncio
@pytest.mark.parametrize('delta',[D('-2'),D('2')])
async def test_rk_posting_metadata_migration_and_control(delta):
    engine=create_async_engine(settings.database_url,poolclass=NullPool)
    schema='test_rk_completion_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                path=next((Path(__file__).parents[1]/'alembic/versions').glob('a1d8e9f0b253*.py'))
                spec=importlib.util.spec_from_file_location('rk_completion_migration',path)
                migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
                def cycle(c):
                    with Operations.context(MigrationContext.configure(c)):
                        migration.downgrade();migration.upgrade()
                await conn.run_sync(cycle)
                async with AsyncSession(conn,expire_on_commit=False) as db:
                    payment,_=await seed(db,False,True)
                    payment.amount=D(120)
                    await db.execute(text('UPDATE payments SET amount=120 WHERE id=1'))
                    db.add(payment);await db.flush()
                    recognized=await reconcile_output_tax_calculation_from_active_sources(db,company_id=1,tax_calculation_id=1,adjustment_date=D1,created_by=1)
                    await _post_created_output_vat_recognition_events(db,result=recognized,created_by=1)
                    await db.execute(text("UPDATE companies SET edrpou='12345678',vat_number='111' WHERE id=1"))
                    await db.execute(text("UPDATE counterparties SET tax_number='87654321',vat_number='222' WHERE id=1"))
                    await db.execute(text("UPDATE products SET base_uom_code='pcs' WHERE id=1"))
                    db.add(ProductTaxClassification(company_id=1,product_id=1,effective_from=D1,classification_kind='uktzed',statutory_code='1234567890',created_by=1))
                    await db.flush()
                    pn=await create_output_tax_invoice(db,company_id=1,source_kind='settlement',source_id=1,document_number='PN-COMPLETE',created_by=1)
                    for status in ('prepared','registered'):
                        await append_tax_invoice_registration_event(db,company_id=1,tax_invoice_id=pn.id,status=status,event_date=D1,reference='receipt' if status=='registered' else None,created_by=1)
                    line=await db.scalar(select(TaxInvoiceLine).where(TaxInvoiceLine.tax_invoice_id==pn.id))
                    event=TradeValueCorrectionEvent(company_id=1,direction='sale',trade_document_id=2,trade_document_line_id=2,product_id=1,
                        correction_date=DAY,original_gross_amount=D(120),original_tax_amount=D(20),corrected_gross_amount=D(120)+delta*6,
                        corrected_tax_amount=D(20)+delta,currency_code='UAH',created_by=1)
                    db.add(event);await db.flush()
                    rk=await create_tax_invoice_correction_idempotent(db,company_id=1,request_key='rk',direction='output',original_tax_invoice_id=pn.id,
                        document_number='RK1',document_date=DAY,sources=[TaxInvoiceCorrectionLineSource(line_number=1,source_kind='sales_value_correction',source_id=event.id,reason_code='price')],created_by=1)
                    assert rk.registration_party==('buyer' if delta<0 else 'seller')
                    async def postings():
                        return list((await db.scalars(select(JournalEntry).where(JournalEntry.tax_invoice_correction_line_id.is_not(None)))).all())
                    assert len(await postings())==(0 if delta<0 else 1)
                    if delta<0:
                        with pytest.raises(HTTPException,match='closed'):
                            async with db.begin_nested():
                                await db.execute(text('UPDATE accounting_periods SET is_locked=true'))
                                await append_tax_invoice_correction_registration_idempotent(db,company_id=1,request_key='locked-reg',tax_invoice_correction_id=rk.id,status='registered',event_date=DAY,reference='receipt-rk',received_on=DAY,created_by=1)
                        await db.refresh(rk)
                    await append_tax_invoice_correction_registration_idempotent(db,company_id=1,request_key='rk-reg',tax_invoice_correction_id=rk.id,status='registered',event_date=DAY,reference='receipt-rk',received_on=DAY,created_by=1)
                    entries=await postings();assert len(entries)==1 and entries[0].entry_date==DAY
                    await append_tax_invoice_correction_registration_idempotent(db,company_id=1,request_key='rk-reg',tax_invoice_correction_id=rk.id,status='registered',event_date=DAY,reference='receipt-rk',received_on=DAY,created_by=1)
                    assert len(await postings())==1
                    with pytest.raises(AccountingReversalError,match='cannot be reversed directly'):
                        await reverse_journal_entry(db,company_id=1,journal_entry_id=entries[0].id,reversal_date=DAY,reversed_by=1)
                    metadata=await create_tax_invoice_correction_idempotent(db,company_id=1,request_key='metadata',direction='output',original_tax_invoice_id=pn.id,
                        document_number='RK-META',document_date=DAY,sources=[TaxInvoiceCorrectionLineSource(line_number=1,source_kind='metadata_correction',source_id=line.id,reason_code='requisite',replacement={'description':'Corrected name'})],created_by=1)
                    amended=await db.scalar(select(TaxInvoiceCorrectionLine).where(TaxInvoiceCorrectionLine.tax_invoice_correction_id==metadata.id))
                    assert amended.tax_amount_delta==0 and amended.description=='Corrected name'
                    assert line.description!='Corrected name'
                    await append_tax_invoice_correction_registration_idempotent(db,company_id=1,request_key='metadata-reg',tax_invoice_correction_id=metadata.id,status='registered',event_date=DAY,reference='receipt-meta',created_by=1)
                    assert len(await postings())==1
                    cutoff=datetime.now(timezone.utc)
                    report=await get_vat_register(db,company_id=1,date_from=D1,date_to=date(2026,9,30),as_of=cutoff)
                    assert report['matched'],report['issues']
                    assert report['totals']['output']['posted_vat']==D(20)+delta
                    declaration_sources=await load_posted_output_corrections(db,company_id=1,period_start=D1,period_end=date(2026,9,30),source_cutoff_at=cutoff)
                    assert len(declaration_sources)==1 and declaration_sources[0]['tax_amount_delta']==delta
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
