from datetime import date
from decimal import Decimal as D
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.models
from app.core.database import Base
from app.core.config import settings
from app.models.counterparty import Counterparty
from app.models.contract import Contract
from app.models.purchase_quote import PurchaseQuote
from app.models.user import User
from app.schemas.purchase_quote import PurchaseQuoteCreate, PurchaseQuoteComparisonRequest
from app.schemas.trade_document import TradeDocumentCreate
from app.services.purchase_quote_service import create_purchase_quote, compare_purchase_quotes, withdraw_purchase_quote, PurchaseQuoteError
from app.services.contract_types import ContractType, ContractStatus
from app.services.trade_document_types import TradeDirection, TradeDocumentKind
from app.api.v1.trade_documents import create_trade_document

pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_E2E") != "1", reason="Set RUN_POSTGRES_E2E=1")
DAY = date(2026, 9, 15)


def module_at(path, name):
    spec=importlib.util.spec_from_file_location(name, path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup(conn):
    Base.metadata.create_all(conn, tables=[t for t in Base.metadata.sorted_tables if t.name != "purchase_quotes"])
    module=module_at(Path(__file__).resolve().parents[1]/'alembic/versions/d1f8b4c6e935_add_purchase_quotes.py','quote_migration')
    with Operations.context(MigrationContext.configure(conn)):
        module.upgrade(); module.downgrade(); module.upgrade()
    def include(obj, name, type_, reflected, compare_to):
        return name == 'purchase_quotes' if type_ == 'table' else getattr(getattr(obj,'table',None),'name',None) == 'purchase_quotes'
    assert compare_metadata(MigrationContext.configure(conn, opts={'include_object':include}), Base.metadata) == []


@pytest.mark.asyncio
async def test_quotes_comparison_tenant_contract_withdrawal_and_order_terms():
    engine=create_async_engine(settings.database_url, poolclass=NullPool)
    schema='test_quotes_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(setup)
                async with AsyncSession(conn, expire_on_commit=False, join_transaction_mode='create_savepoint') as db:
                    foundation=module_at(Path(__file__).with_name('test_purchase_landed_cost_postgresql.py'),'quotes_seed')
                    await foundation.seed(db)
                    supplier=await db.get(Counterparty,1)
                    supplier.payment_term_days=7
                    db.add(Counterparty(id=2,company_id=1,name='Alternative supplier',counterparty_type='supplier'))
                    db.add(Contract(id=1,company_id=1,counterparty_id=1,number='PURCHASE-TERMS',
                        contract_type=ContractType.PURCHASE,status=ContractStatus.ACTIVE,start_date=date(2026,1,1),
                        end_date=date(2026,12,31),currency_code='UAH',payment_term_days=30))
                    await db.flush()
                    data=PurchaseQuoteCreate(supplier_id=1,contract_id=1,product_id=1,reference='OFFER-1',min_quantity=D('1'),
                        unit_price_net=D('8'),unit_price_gross=D('12'),lead_time_days=2,payment_term_days=30,
                        valid_from=DAY,valid_until=date(2026,9,30))
                    quote=await create_purchase_quote(db,company_id=1,data=data,created_by=1)
                    assert (await create_purchase_quote(db,company_id=1,data=data,created_by=1)).id == quote.id
                    with pytest.raises(PurchaseQuoteError,match='different terms'):
                        await create_purchase_quote(db,company_id=1,data=data.model_copy(update={'unit_price_gross':D('13')}),created_by=1)
                    with pytest.raises(PurchaseQuoteError,match='company/product'):
                        await create_purchase_quote(db,company_id=2,data=data,created_by=1)
                    alternative=await create_purchase_quote(db,company_id=1,
                        data=data.model_copy(update={'supplier_id':2,'contract_id':None,'reference':'OFFER-2',
                            'unit_price_net':D('9'),'unit_price_gross':D('9'),'delivery_net':D('20'),'delivery_gross':D('20')}),created_by=1)
                    request=PurchaseQuoteComparisonRequest(product_id=1,quantity=D('10'),order_date=DAY,required_delivery_date=date(2026,9,17))
                    comparison=await compare_purchase_quotes(db,company_id=1,request=request)
                    assert comparison.recommended_quote_id == alternative.id
                    assert comparison.ranked[0].total_payable == D('110')
                    await withdraw_purchase_quote(db,company_id=1,quote_id=alternative.id,withdrawn_by=1)
                    comparison=await compare_purchase_quotes(db,company_id=1,request=request)
                    assert comparison.recommended_quote_id == quote.id
                    assert comparison.rejected[0].reasons == ['quote_withdrawn']
                    supplier.is_active=False
                    await db.flush()
                    comparison=await compare_purchase_quotes(db,company_id=1,request=request)
                    assert comparison.recommended_quote_id is None
                    supplier.is_active=True
                    await db.flush()
                    user=await db.get(User,1)
                    base=dict(direction=TradeDirection.PURCHASE,kind=TradeDocumentKind.ORDER,document_date=DAY,
                        counterparty_id=1,contract_id=1,lines=[dict(product_id=1,warehouse_id=1,quantity='2',unit_price='10')])
                    order=await create_trade_document(company_id=1,data=TradeDocumentCreate(number='INHERITED',**base),current_user=user,db=db,_permission=None)
                    assert order.payment_term_days == 30
                    immediate=await create_trade_document(company_id=1,data=TradeDocumentCreate(number='IMMEDIATE',payment_term_days=0,**base),current_user=user,db=db,_permission=None)
                    assert immediate.payment_term_days == 0
                    from app.services.trade_document_lifecycle_service import confirm_purchase_order, PurchaseOrderCounterpartyInvalidError
                    supplier.counterparty_type='customer'
                    await db.flush()
                    with pytest.raises(PurchaseOrderCounterpartyInvalidError, match='supplier'):
                        await confirm_purchase_order(db, company_id=1, document_id=order.id)
                    supplier.counterparty_type='both'
                    await db.flush()
                    await confirm_purchase_order(db, company_id=1, document_id=order.id)
                    assert order.payment_term_days == 30

                    # Authenticated users without company permissions cannot add offers.
                    from fastapi import FastAPI
                    from httpx import ASGITransport, AsyncClient
                    from app.api.v1.purchase_quotes import router
                    from app.api.deps import get_current_user
                    from app.core.database import get_db
                    app=FastAPI()
                    app.include_router(router)
                    app.dependency_overrides[get_current_user]=lambda: user
                    app.dependency_overrides[get_db]=lambda: db
                    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
                        response=await client.post('/companies/1/purchase-quotes',json=data.model_dump(mode='json'))
                        assert response.status_code == 403
                    assert await db.scalar(select(PurchaseQuote.id).where(PurchaseQuote.reference=='OFFER-1')) == quote.id
            finally:
                await tx.rollback()
            assert await conn.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=:name'),{'name':schema}) == 0
    finally:
        await engine.dispose()
