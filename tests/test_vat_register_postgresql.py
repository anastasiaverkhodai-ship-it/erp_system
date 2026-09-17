"""Actual PostgreSQL snapshot, Decimal transport, company isolation and rollback."""
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool
import app.models
from app.core.config import settings
from app.core.database import Base
from app.models.product_tax_classification import ProductTaxClassification
from app.services.tax_invoice_output_persistence_service import create_output_tax_invoice
from app.services.tax_invoice_registration_lifecycle_service import append_tax_invoice_registration_event
from app.services.vat_register_service import get_vat_register, load_snapshot
from app.services.tax_recognition_reconciliation_service import reconcile_output_tax_calculation_from_active_sources
from app.services.tax_recognition_lifecycle_service import _post_created_output_vat_recognition_events
from test_vat_first_event_postgresql import seed, D1, D4


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')
async def test_postgresql_snapshot_detects_missing_pn_and_wrong_gl_without_writes():
    engine=create_async_engine(settings.database_url,poolclass=NullPool)
    schema='test_vat_register_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                async with AsyncSession(conn,expire_on_commit=False) as db:
                    payment,_=await seed(db,False,True)
                    db.add(payment);await db.flush()
                    result=await reconcile_output_tax_calculation_from_active_sources(db,company_id=1,tax_calculation_id=1,adjustment_date=D1,created_by=1)
                    await _post_created_output_vat_recognition_events(db,result=result,created_by=1)
                    await db.flush()
                    cutoff=datetime.now(timezone.utc)
                    snap=await load_snapshot(db,company_id=1)
                    assert isinstance(snap['tax_recognition_events'][0]['recognized_tax_amount'],Decimal)
                    async def control():
                        return await get_vat_register(db,company_id=1,date_from=D1,date_to=D4,as_of=cutoff)
                    report=await control()
                    assert report['totals']['output']['expected_vat']==Decimal(12)
                    assert report['totals']['output']['posted_vat']==Decimal(12),report
                    assert [i['code'] for i in report['issues']]==['missing_tax_document']
                    # Build a legal PN through the actual persistence service.
                    await db.execute(text("UPDATE companies SET edrpou='12345678',vat_number='111' WHERE id=1"))
                    await db.execute(text("UPDATE counterparties SET tax_number='87654321',vat_number='222' WHERE id=1"))
                    await db.execute(text("UPDATE products SET base_uom_code='pcs' WHERE id=1"))
                    await db.execute(text("UPDATE trade_documents SET vat_policy_id=1,counterparty_vat_registration_id=1 WHERE id=1"))
                    db.add(ProductTaxClassification(company_id=1,product_id=1,effective_from=D1,classification_kind='uktzed',statutory_code='1234567890',created_by=1))
                    await db.flush()
                    invoice=await create_output_tax_invoice(db,company_id=1,source_kind='settlement',source_id=1,document_number='PN-CONTROL',created_by=1)
                    await append_tax_invoice_registration_event(db,company_id=1,tax_invoice_id=invoice.id,status='prepared',event_date=D1,reference=None,created_by=1)
                    await append_tax_invoice_registration_event(db,company_id=1,tax_invoice_id=invoice.id,status='registered',event_date=D1,reference='receipt-control',created_by=1)
                    await db.flush()
                    # Separate recorded instants model later committed work while
                    # preserving the rollback-only fixture (PG now() is transaction time).
                    await db.execute(text('UPDATE tax_invoices SET created_at=:recorded'),{'recorded':datetime.now(timezone.utc)})
                    # Earlier cutoff must not see PN/registration recorded later.
                    assert 'missing_tax_document' in [i['code'] for i in (await control())['issues']]
                    cutoff=datetime.now(timezone.utc)
                    complete=await control()
                    assert complete['matched'],complete['issues']
                    assert complete['register_totals']['output']['tax_amount']==Decimal(12)
                    await db.execute(text("UPDATE journal_entries SET entry_date='2026-10-01'"))
                    shifted=await control()
                    assert shifted['totals']['output']['difference']==-Decimal(12)
                    assert 'journal_date_mismatch' in [i['code'] for i in shifted['issues']]
                    assert await conn.scalar(text('SELECT count(*) FROM tax_recognition_events'))==1
                    assert await conn.scalar(text('SELECT count(*) FROM journal_entries'))==1
                    other=await load_snapshot(db,company_id=999)
                    assert all(not values for values in other.values())
                    # Declaration adapter is opt-in; base 10.8 schema works without it.
                    await db.execute(text('DROP TABLE IF EXISTS vat_declaration_source_lines'))
                    await control()
            finally:
                await tx.rollback()
            assert await conn.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=:s'),{'s':schema})==0
    finally:
        await engine.dispose()
