"""Attestation, economic source, credit events and GL share one transaction."""
import importlib.util
import os
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool
from alembic.migration import MigrationContext
from alembic.operations import Operations
import app.models
from app.core.config import settings
from app.core.database import Base
from app.models.input_vat_credit_claim import InputVatCreditClaim
from app.models.tax_credit_evidence import TaxCreditEvidence
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.models.journal_entry import JournalEntry
from app.services.input_vat_credit_claim_service import create_input_vat_credit_claim, reverse_input_vat_credit_claim, InputVatCreditClaimError
from test_vat_first_event_postgresql import seed, D1, D4
from test_input_vat_credit_eligibility import payload

pytestmark=pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')


@pytest.mark.asyncio
@pytest.mark.parametrize('late',[False,True])
async def test_credit_claim_persistence_period_reversal_and_atomic_rejection(late):
    engine=create_async_engine(settings.database_url,poolclass=NullPool)
    schema='test_input_claim_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                # Exercise actual migration round trip without touching public data.
                path=next((Path(__file__).parents[1]/'alembic/versions').glob('a4cbd7f9b268*.py'))
                spec=importlib.util.spec_from_file_location('claim_migration',path)
                migration=importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
                def cycle(c):
                    with Operations.context(MigrationContext.configure(c)):
                        migration.downgrade(); migration.upgrade()
                await conn.run_sync(cycle)
                async with AsyncSession(conn,expire_on_commit=False) as db:
                    payment,_=await seed(db,True,True)
                    await db.execute(text('DELETE FROM tax_credit_evidence'))
                    # Establish economic first event and registration snapshots in August for the late case.
                    if late:
                        await db.execute(text("UPDATE company_vat_policies SET effective_from='2026-08-01' WHERE id=1"))
                        await db.execute(text("UPDATE counterparty_vat_registrations SET effective_from='2026-08-01'"))
                        await db.execute(text("UPDATE payments SET payment_date='2026-08-10'"))
                    data=payload(invoice_date='2026-08-10' if late else '2026-09-01',
                        registered_on='2026-09-06' if late else '2026-09-02',claim_period='2026-09-01')
                    async def create(value=data, company=1):
                        return await create_input_vat_credit_claim(db,company_id=company,data=value,created_by=1)
                    async def count(model):
                        return await db.scalar(select(func.count()).select_from(model))
                    async def net():
                        value = await db.scalar(text('SELECT coalesce(sum(CASE WHEN reversal_of_id IS NULL THEN recognized_tax_amount ELSE -recognized_tax_amount END),0) FROM tax_recognition_events'))
                        from app.services.accounting_account_role_resolver import resolve_company_account_roles
                        from app.services.accounting_account_roles import AccountingAccountRole
                        roles = await resolve_company_account_roles(db, company_id=1, roles=[AccountingAccountRole.TAX_SETTLEMENT])
                        account_id = roles[AccountingAccountRole.TAX_SETTLEMENT].id
                        gl = await db.scalar(text("SELECT coalesce(sum(l.debit-l.credit),0) FROM journal_entry_lines l JOIN journal_entries j ON j.id=l.journal_entry_id WHERE j.company_id=1 AND j.status IN ('posted','reversed') AND l.account_id=:a"), {'a': account_id})
                        assert gl == value
                        return value
                    # Registered legal evidence alone does not create economic capacity.
                    with pytest.raises(InputVatCreditClaimError,match='economic capacity'):
                        await create()
                    assert await count(TaxCreditEvidence)==0
                    db.add(payment); await db.flush()
                    for change, message in [
                        ({'registration_status':'suspended'},'registration_not_confirmed'),
                        ({'supplier_vat_number':'wrong'},'VAT numbers'),
                        ({'taxable_base':'70','tax_amount':'14'},'economic capacity'),
                        ({'taxable_base':'60','tax_amount':'15'},'calculation rate'),
                    ]:
                        changed=type(data).model_validate(data.model_dump() | change)
                        with pytest.raises(InputVatCreditClaimError,match=message):
                            await create(changed)
                        assert await count(TaxCreditEvidence)==0
                    with pytest.raises(InputVatCreditClaimError,match='not found'):
                        await create(company=2)
                    with pytest.raises(HTTPException,match='closed'):
                        async with db.begin_nested():
                            await db.execute(text("UPDATE accounting_periods SET status='closed', is_locked=true"))
                            await create()
                    assert await count(InputVatCreditClaim)==0 and await count(TaxCreditEvidence)==0
                    claim=await create()
                    assert await net()==D(12)
                    from app.services.trade_document_lifecycle_service import cancel_purchase_invoice, TradeInvoiceStatusError
                    with pytest.raises(TradeInvoiceStatusError,match='active INPUT VAT'):
                        await cancel_purchase_invoice(db,company_id=1,document_id=2)
                    event=await db.scalar(select(TaxRecognitionEvent))
                    assert event.recognition_date==date(2026,9,6 if late else 1)
                    journal=await db.scalar(select(JournalEntry))
                    assert journal.entry_date==event.recognition_date
                    assert (await create()).id==claim.id
                    assert await count(InputVatCreditClaim)==1 and await count(TaxRecognitionEvent)==1
                    with pytest.raises(InputVatCreditClaimError,match='different data'):
                        await create(data.model_copy(update={'receipt_reference':'different'}))
                    reversal_day=date(2026,9,10)
                    with pytest.raises(HTTPException,match='closed'):
                        async with db.begin_nested():
                            await db.execute(text("UPDATE accounting_periods SET status='closed', is_locked=true"))
                            await reverse_input_vat_credit_claim(db,company_id=1,claim_id=claim.id,reversal_date=reversal_day,reversed_by=1)
                    assert await net()==D(12)
                    await db.refresh(claim)
                    assert claim.reversal_evidence_id is None
                    await reverse_input_vat_credit_claim(db,company_id=1,claim_id=claim.id,reversal_date=reversal_day,reversed_by=1)
                    assert await net()==0 and claim.reversal_evidence_id is not None
                    await reverse_input_vat_credit_claim(db,company_id=1,claim_id=claim.id,reversal_date=reversal_day,reversed_by=1)
                    assert await count(TaxRecognitionEvent)==2
                    assert await db.scalar(text('SELECT count(*) FROM (SELECT journal_entry_id FROM journal_entry_lines GROUP BY journal_entry_id HAVING sum(debit) != sum(credit)) t'))==0
            finally:
                await tx.rollback()
            assert await conn.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=:s'),{'s':schema})==0
    finally:
        await engine.dispose()
