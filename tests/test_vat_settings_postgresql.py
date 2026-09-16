"""Real PostgreSQL migration, API snapshots, strict confirmation and tenant boundaries."""
from datetime import date
import importlib.util
import os
from pathlib import Path
from uuid import uuid4
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
import app.models
from app.core.database import Base
from app.core.config import settings
from app.models.company import Company
from app.models.counterparty import Counterparty
from app.models.company_vat_policy import CompanyVatPolicy
from app.models.trade_document import TradeDocument
from app.models.tax_calculation import TaxCalculation
from app.models.user import User
from app.schemas.company_vat_policy import CompanyVatPolicyCreate, CounterpartyVatRegistrationCreate
from app.schemas.trade_document import TradeDocumentCreate, TradeDocumentUpdate
from app.services.company_vat_policy_service import append_vat_policy, append_counterparty_vat_registration, VatPolicyError
from app.services.trade_document_lifecycle_service import confirm_purchase_invoice, TradeInvoiceCompanyInvalidError
from app.api.v1.trade_documents import create_trade_document, update_trade_document

pytestmark = pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='Set RUN_POSTGRES_E2E=1')
DAY=date(2026,9,16)


def load(path, name):
    spec=importlib.util.spec_from_file_location(name, path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup(conn):
    Base.metadata.create_all(conn)
    migration=load(Path(__file__).resolve().parents[1] / 'alembic/versions/e2a9c5d7f046_add_effective_dated_vat_settings_and_.py','vat_settings_migration')
    with Operations.context(MigrationContext.configure(conn)):
        migration.downgrade()
        migration.upgrade()
        migration.downgrade()
        migration.upgrade()
    assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []


@pytest.mark.asyncio
async def test_vat_settings_history_snapshots_confirmation_migration_and_rbac():
    engine=create_async_engine(settings.database_url, poolclass=NullPool)
    schema='test_vat_settings_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(setup)
                async with AsyncSession(conn,expire_on_commit=False,join_transaction_mode='create_savepoint') as db:
                    seed=load(Path(__file__).with_name('test_purchase_landed_cost_postgresql.py'),'vat_settings_seed')
                    await seed.seed(db)
                    supplier=await db.get(Counterparty,1)
                    supplier.counterparty_type='supplier'
                    await db.flush()
                    company=await db.get(Company,1)
                    assert company.vat_policy_enabled is False
                    user=await db.get(User,1)
                    data=CompanyVatPolicyCreate(effective_from=DAY,payer_status='vat_payer',vat_number='123456789012',legal_basis='Registration extract 16/09')
                    first=await append_vat_policy(db,company_id=1,data=data,created_by=1)
                    assert company.vat_policy_enabled
                    assert (await append_vat_policy(db,company_id=1,data=data,created_by=1)).id == first.id
                    with pytest.raises(VatPolicyError,match='different terms'):
                        await append_vat_policy(db,company_id=1,data=data.model_copy(update={'allow_cash_method':True}),created_by=1)
                    partydata=CounterpartyVatRegistrationCreate(effective_from=date(2026,9,15),payer_status='vat_payer',vat_number='222222222222',legal_basis='Supplier extract')
                    party=await append_counterparty_vat_registration(db,company_id=1,counterparty_id=1,data=partydata,created_by=1)
                    assert (await append_counterparty_vat_registration(db,company_id=1,counterparty_id=1,data=partydata,created_by=1)).id == party.id
                    with pytest.raises(VatPolicyError,match='not found'):
                        await append_counterparty_vat_registration(db,company_id=2,counterparty_id=1,data=partydata,created_by=1)

                    async def make(number, day=DAY, **tax):
                        payload=TradeDocumentCreate.model_validate(dict(number=number,direction='purchase',kind='invoice',
                            document_date=day,counterparty_id=1,lines=[dict(product_id=1,quantity='3',unit_price='.17',**tax)]))
                        return await create_trade_document(company_id=1,data=payload,current_user=user,db=db,_permission=None)
                    blank=await make('UNCLASSIFIED')
                    with pytest.raises(TradeInvoiceCompanyInvalidError,match='classification'):
                        await confirm_purchase_invoice(db,company_id=1,document_id=blank.id)
                    assert blank.status == 'draft'
                    # Draft edit path must preserve the explicit legal basis/category.
                    blank = await update_trade_document(company_id=1, document_id=blank.id,
                        data=TradeDocumentUpdate.model_validate({'lines':[dict(product_id=1,quantity='3',unit_price='.17',
                            tax_rate_code='VAT_EXEMPT',tax_recognition_method='manual',tax_price_mode='exclusive',tax_legal_basis='197 / verified operation')]}),
                        db=db,_permission=None)
                    assert blank.lines[0].tax_rate_code == 'VAT_EXEMPT'
                    assert blank.lines[0].tax_legal_basis == '197 / verified operation'
                    before=await make('BEFORE-POLICY', date(2026,9,15), tax_rate_code='VAT20',tax_recognition_method='first_event',tax_price_mode='exclusive')
                    with pytest.raises(TradeInvoiceCompanyInvalidError,match='No VAT policy'):
                        await confirm_purchase_invoice(db,company_id=1,document_id=before.id)
                    # Appending a future status does not change the earlier document's selected policy.
                    future=await append_vat_policy(db,company_id=1,data=CompanyVatPolicyCreate(
                        effective_from=date(2026,10,1),payer_status='non_vat_payer',legal_basis='Deregistration'),created_by=1)
                    taxable=await make('TAXABLE',tax_rate_code='VAT20',tax_recognition_method='first_event',tax_price_mode='exclusive')
                    await confirm_purchase_invoice(db,company_id=1,document_id=taxable.id)
                    await db.flush()
                    assert taxable.vat_policy_id == first.id and taxable.counterparty_vat_registration_id == party.id
                    calc=await db.scalar(select(TaxCalculation).where(TaxCalculation.trade_document_id==taxable.id))
                    assert str(calc.tax_amount) == '0.10'
                    with pytest.raises(VatPolicyError):
                        await append_vat_policy(db,company_id=1,data=data.model_copy(update={'effective_from':date(2026,9,17)}),created_by=1)
                    with pytest.raises(VatPolicyError,match='previously confirmed'):
                        await append_counterparty_vat_registration(db,company_id=1,counterparty_id=1,
                            data=partydata.model_copy(update={'effective_from':DAY}),created_by=1)

                    exempt=await make('EXEMPT',tax_rate_code='VAT_EXEMPT',tax_recognition_method='manual',tax_price_mode='exclusive',tax_legal_basis='Documented exemption for this operation')
                    await confirm_purchase_invoice(db,company_id=1,document_id=exempt.id)
                    await db.flush()
                    assert exempt.lines[0].tax_legal_basis == 'Documented exemption for this operation'
                    assert await db.scalar(select(TaxCalculation.id).where(TaxCalculation.trade_document_id==exempt.id)) is None
                    futureinvoice=await make('NONPAYER-VAT',date(2026,10,1),tax_rate_code='VAT20',tax_recognition_method='first_event',tax_price_mode='exclusive')
                    with pytest.raises(TradeInvoiceCompanyInvalidError,match='gross-cost'):
                        await confirm_purchase_invoice(db,company_id=1,document_id=futureinvoice.id)

                    # A non-payer buyer must not disguise a VAT-payer supplier as non-payer.
                    misleading=await make('WRONG-SELLER-STATUS',date(2026,10,1),no_vat_reason='non_vat_payer',tax_legal_basis='Wrong classification')
                    with pytest.raises(TradeInvoiceCompanyInvalidError,match='cannot use non_vat_payer'):
                        await confirm_purchase_invoice(db,company_id=1,document_id=misleading.id)
                    # Supplier deregistration changes only future confirmations.
                    nonpayer_party=await append_counterparty_vat_registration(db,company_id=1,counterparty_id=1,
                        data=CounterpartyVatRegistrationCreate(effective_from=date(2026,10,1),payer_status='non_vat_payer',legal_basis='Deregistration extract'),created_by=1)
                    clean=await make('NONPAYER-SUPPLIER',date(2026,10,1),no_vat_reason='non_vat_payer',tax_legal_basis='Supplier registration extract')
                    await confirm_purchase_invoice(db,company_id=1,document_id=clean.id)
                    await db.flush()
                    assert clean.vat_policy_id == future.id and clean.counterparty_vat_registration_id == nonpayer_party.id
                    assert await db.scalar(select(TaxCalculation.id).where(TaxCalculation.trade_document_id==clean.id)) is None

                    # Composite FK prevents a document from pointing to another company's policy.
                    other=await append_vat_policy(db,company_id=2,data=data,created_by=1)
                    with pytest.raises(IntegrityError):
                        async with db.begin_nested():
                            await db.execute(text('UPDATE trade_documents SET vat_policy_id=:p WHERE id=:i'),{'p':other.id,'i':taxable.id})
                            await db.flush()

                    from fastapi import FastAPI
                    from httpx import ASGITransport, AsyncClient
                    from app.api.v1.company_vat_policies import router
                    from app.api.deps import get_current_user
                    from app.core.database import get_db
                    app=FastAPI(); app.include_router(router)
                    app.dependency_overrides[get_current_user]=lambda: user
                    app.dependency_overrides[get_db]=lambda: db
                    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
                        assert (await client.post('/companies/1/vat-settings/policies',json=data.model_dump(mode='json'))).status_code == 403
                        assert (await client.get('/companies/2/vat-settings')).status_code == 403
            finally:
                await tx.rollback()
            assert await conn.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=:s'),{'s':schema}) == 0
    finally:
        await engine.dispose()
