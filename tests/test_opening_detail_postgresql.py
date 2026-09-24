"""Cutover detail uses real stock and settleable debts, with no second GL/VAT."""
import os
from datetime import date, timedelta
from decimal import Decimal as D
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
import app.models
from app.core.config import settings
from app.core.database import Base
from app.models.company import Company
from app.models.user import User
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.models.counterparty import Counterparty
from app.models.counterparty_open_item import CounterpartyOpenItem
from app.models.journal_entry import JournalEntry
from app.models.stock_balance import StockBalance
from app.models.payment import Payment
from app.schemas.opening_balance_detail import OpeningPackageCreate
from app.services.company_chart_of_accounts_seeding_service import seed_company_chart_of_accounts
from app.services.opening_balance_detail_service import create_opening_package, attach_opening_details
from app.services.opening_balance_service import create_opening_balance, post_opening_balance, reverse_opening_balance, OpeningBalanceError
from app.services.payment_settlement_service import create_payment_settlement_allocation, reverse_payment_settlement_allocation
from app.services.payment_journal_service import generate_and_post_payment_journal_entry
from app.services.sales_ar_aging_projection_service import load_sales_ar_aging_projection
from app.services.accounting_control_service import get_consolidated_accounting_controls
from app.services.accounting_reversal import reverse_journal_entry, AccountingReversalError
from app.services.document_reversal import reverse_document, DocumentReversalError

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')]
DAY=date.today().replace(day=1)


@pytest_asyncio.fixture
async def detail_engine():
    admin=create_async_engine(settings.database_url,poolclass=NullPool)
    schema='test_opening_detail_'+uuid4().hex
    engine=create_async_engine(settings.database_url,poolclass=NullPool,
        connect_args={'server_settings':{'search_path':schema}})
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine,expire_on_commit=False) as db:
            db.add_all([Company(id=1,name='Opening'),Company(id=2,name='Other'),
                User(id=1,email='detail@example.test',password_hash='unused',first_name='Test',last_name='User')])
            await db.flush()
            db.add_all([Product(id=1,company_id=1,name='Goods',sku='OPEN'),Warehouse(id=1,company_id=1,name='Main'),
                Counterparty(id=1,company_id=1,name='Party',counterparty_type='both'),
                AccountingPeriod(company_id=1,year=DAY.year,month=DAY.month,start_date=DAY,
                    end_date=(DAY.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1),status='open')])
            await seed_company_chart_of_accounts(session=db,company_id=1)
            db.add(Account(company_id=1,code="401",name="Opening equity",account_type="equity",normal_balance="credit"))
            await db.commit()
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def payload(db):
    ids={a.code:a.id for a in (await db.scalars(select(Account).where(Account.company_id==1))).all()}
    return OpeningPackageCreate(opening=dict(request_key='cutover',opening_date=DAY,lines=[
        dict(account_id=ids['281'],debit=100),dict(account_id=ids['361'],debit=60),
        dict(account_id=ids['631'],credit=40),dict(account_id=ids['401'],credit=120)]),
        details=dict(stock=[dict(product_id=1,warehouse_id=1,quantity=10,unit_cost=10)],debts=[
            dict(reference='OLD-AR',counterparty_id=1,item_type='receivable',document_date=DAY-timedelta(days=20),due_date=DAY-timedelta(days=10),amount=60),
            dict(reference='OLD-AP',counterparty_id=1,item_type='payable',document_date=DAY-timedelta(days=20),due_date=DAY+timedelta(days=10),amount=40)]))


@pytest.mark.parametrize('mode',['package','attach'])
@pytest.mark.parametrize('method',['fifo','weighted_average_moving'])
async def test_cutover_stock_debts_settlement_and_reversal(detail_engine,mode,method):
    async with AsyncSession(detail_engine,expire_on_commit=False) as db:
        (await db.get(Company,1)).inventory_valuation_method=method
        data=await payload(db)
        if mode=='package':
            opening=await create_opening_package(db,company_id=1,created_by=1,data=data)
        else:
            opening=await create_opening_balance(db,1,1,data.opening)
            await post_opening_balance(db,1,opening.id)
            await attach_opening_details(db,company_id=1,opening_balance_id=opening.id,created_by=1,data=data.details)
        await attach_opening_details(db,company_id=1,opening_balance_id=opening.id,created_by=1,data=data.details)
        assert await db.scalar(select(func.count()).select_from(JournalEntry))==1
        assert await db.scalar(select(StockBalance.quantity))==10
        assert len(await load_sales_ar_aging_projection(db,company_id=1,as_of_date=DAY))==1
        assert await load_sales_ar_aging_projection(db,company_id=1,as_of_date=DAY-timedelta(days=1))==()
        with pytest.raises(AccountingReversalError,match='lifecycle'):
            await reverse_journal_entry(db,1,opening.journal_entry_id,date.today(),1)
        stock_id=await db.scalar(text('SELECT stock_document_id FROM opening_balance_details'))
        with pytest.raises(DocumentReversalError,match='lifecycle'):
            await reverse_document(db,1,stock_id,date.today(),1)
        items=(await db.scalars(select(CounterpartyOpenItem).order_by(CounterpartyOpenItem.id))).all()
        allocations=[]
        from datetime import datetime,timezone
        for item in items:
            payment=Payment(company_id=1,counterparty_id=1,number='P-'+str(item.id),
                direction='incoming' if item.item_type=='receivable' else 'outgoing',status='confirmed',
                confirmed_at=datetime.now(timezone.utc),payment_date=date.today(),currency_code='UAH',amount=item.original_amount,created_by=1)
            db.add(payment);await db.flush()
            await generate_and_post_payment_journal_entry(db,payment=payment,created_by=1)
            allocation=await create_payment_settlement_allocation(db,company_id=1,payment_id=payment.id,
                open_item_id=item.id,amount=item.original_amount,created_by=1)
            allocations.append(allocation)
        assert await db.scalar(text('SELECT count(*) FROM tax_recognition_events'))==0
        report=await get_consolidated_accounting_controls(db,company_id=1,date_from=DAY,date_to=date.today())
        assert report.matched,report
        with pytest.raises(OpeningBalanceError,match='settlements'):
            await reverse_opening_balance(db,1,opening.id,date.today(),1)
        for allocation in allocations:
            await reverse_payment_settlement_allocation(db,company_id=1,allocation_id=allocation.id,reversed_by=1)
        reverse=await reverse_opening_balance(db,1,opening.id,date.today(),1)
        assert (await reverse_opening_balance(db,1,opening.id,date.today(),1)).id==reverse.id
        assert await db.scalar(select(StockBalance.quantity))==0
        assert await load_sales_ar_aging_projection(db,company_id=1,as_of_date=date.today())==()
        report=await get_consolidated_accounting_controls(db,company_id=1,date_from=DAY,date_to=date.today())
        assert report.matched,report


async def test_invalid_detail_rolls_back_whole_package(detail_engine):
    async with AsyncSession(detail_engine,expire_on_commit=False) as db:
        data=await payload(db)
        data.details.stock[0].unit_cost=D(11)
        with pytest.raises(OpeningBalanceError,match='exactly match'):
            async with db.begin_nested():
                await create_opening_package(db,company_id=1,created_by=1,data=data)
        assert await db.scalar(select(func.count()).select_from(JournalEntry))==0
        assert await db.scalar(select(func.count()).select_from(CounterpartyOpenItem))==0


async def test_detail_conflicts_and_posted_attachment_are_atomic(detail_engine):
    from fastapi import HTTPException
    from app.models.opening_balance_detail import OpeningBalanceDetail
    async with AsyncSession(detail_engine,expire_on_commit=False) as db:
        data=await payload(db)
        opening=await create_opening_balance(db,1,1,data.opening)
        oid=opening.id
        with pytest.raises(OpeningBalanceError,match='posted'):
            await attach_opening_details(db,company_id=1,opening_balance_id=oid,created_by=1,data=data.details)
        await post_opening_balance(db,1,oid)
        await db.commit()
        with pytest.raises(OpeningBalanceError):
            await attach_opening_details(db,company_id=2,opening_balance_id=oid,created_by=1,data=data.details)
        bad=data.details.model_copy(deep=True);bad.stock[0].product_id=999
        with pytest.raises(OpeningBalanceError,match='product'):
            async with db.begin_nested():
                await attach_opening_details(db,company_id=1,opening_balance_id=oid,created_by=1,data=bad)
        assert await db.scalar(select(func.count()).select_from(OpeningBalanceDetail))==0
        with pytest.raises(HTTPException):
            async with db.begin_nested():
                await db.execute(text("UPDATE accounting_periods SET status='closed',is_locked=true"))
                await attach_opening_details(db,company_id=1,opening_balance_id=oid,created_by=1,data=data.details)
        await attach_opening_details(db,company_id=1,opening_balance_id=oid,created_by=1,data=data.details)
        changed=data.details.model_copy(deep=True);changed.debts[0].reference='DIFFERENT'
        with pytest.raises(OpeningBalanceError,match='different'):
            await attach_opening_details(db,company_id=1,opening_balance_id=oid,created_by=1,data=changed)
        # Independent control detects source corruption even though total GL balances.
        await db.execute(text("UPDATE counterparty_open_items SET original_amount=61 WHERE item_type='receivable'"))
        report=await get_consolidated_accounting_controls(db,company_id=1,date_from=DAY,date_to=date.today())
        assert not report.matched


async def test_concurrent_attachment_creates_one_subledger_package(detail_engine):
    import asyncio
    async with AsyncSession(detail_engine,expire_on_commit=False) as db:
        data=await payload(db)
        opening=await create_opening_balance(db,1,1,data.opening)
        await post_opening_balance(db,1,opening.id)
        oid=opening.id
        await db.commit()
    async def attach():
        async with AsyncSession(detail_engine,expire_on_commit=False) as db:
            await attach_opening_details(db,company_id=1,opening_balance_id=oid,created_by=1,data=data.details)
            await db.commit()
    await asyncio.wait_for(asyncio.gather(attach(),attach()),timeout=25)
    async with AsyncSession(detail_engine) as db:
        assert await db.scalar(text('SELECT count(*) FROM opening_balance_details'))==1
        assert await db.scalar(text('SELECT count(*) FROM documents'))==1
        assert await db.scalar(text('SELECT count(*) FROM counterparty_open_items'))==2
        assert await db.scalar(text('SELECT count(*) FROM journal_entries'))==1
        assert await db.scalar(select(StockBalance.quantity))==10


@pytest.mark.parametrize('method',['fifo','weighted_average_moving'])
async def test_opening_stock_can_be_issued_and_cannot_be_reversed_while_consumed(detail_engine,method):
    from app.models.document import Document
    from app.models.document_line import DocumentLine
    from app.models.accounting_rule import AccountingRule
    from app.models.accounting_rule_line import AccountingRuleLine
    from app.services.document_posting import post_document
    async with AsyncSession(detail_engine,expire_on_commit=False) as db:
        (await db.get(Company,1)).inventory_valuation_method=method
        data=await payload(db)
        opening=await create_opening_package(db,company_id=1,created_by=1,data=data)
        ids={a.code:a.id for a in (await db.scalars(select(Account).where(Account.company_id==1))).all()}
        rule=AccountingRule(company_id=1,code='OPEN-ISSUE',name='Issue opening goods',document_type='issue',lines=[
            AccountingRuleLine(line_no=1,account_id=ids['902'],side='debit',amount_source='inventory_cost'),
            AccountingRuleLine(line_no=2,account_id=ids['281'],side='credit',amount_source='inventory_cost')])
        db.add(rule);await db.flush()
        issue=Document(company_id=1,number='ISSUE-OPEN',document_type='issue',document_date=date.today(),
            status='draft',created_by=1,lines=[DocumentLine(product_id=1,warehouse_id=1,quantity=D(3),price=D(10))])
        db.add(issue);await db.flush()
        await post_document(db,company_id=1,document_id=issue.id,accounting_rule_id=rule.id,created_by=1)
        assert await db.scalar(select(StockBalance.quantity))==7
        assert await db.scalar(text('SELECT cost_amount FROM inventory_cost_entries'))==30
        with pytest.raises(OpeningBalanceError):
            async with db.begin_nested():
                await reverse_opening_balance(db,1,opening.id,date.today(),1)
        await reverse_document(db,1,issue.id,date.today(),1)
        if method=='weighted_average_moving':
            # Canonical MA chronology remains strict even after a later issue was reversed.
            with pytest.raises(OpeningBalanceError,match='later inventory movements'):
                async with db.begin_nested():
                    await reverse_opening_balance(db,1,opening.id,date.today(),1)
            assert await db.scalar(select(StockBalance.quantity))==10
            return
        await reverse_opening_balance(db,1,opening.id,date.today(),1)
        # A correction is a new cutover after the original history was reversed.
        replacement=data.model_copy(deep=True)
        replacement.opening.request_key='replacement'
        replacement.opening.opening_date=date.today()
        await create_opening_package(db,company_id=1,created_by=1,data=replacement)
        assert await db.scalar(select(StockBalance.quantity))==10


async def test_detail_migration_roundtrip_matches_metadata_and_preserves_data(detail_engine):
    from importlib.util import module_from_spec, spec_from_file_location
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.autogenerate import compare_metadata
    spec=spec_from_file_location('opening_detail_migration','alembic/versions/d1552cd41bf1_opening_subledger_detail.py')
    migration=module_from_spec(spec);spec.loader.exec_module(migration)
    async with detail_engine.begin() as conn:
        def roundtrip(sync):
            context=MigrationContext.configure(sync)
            with Operations.context(context):
                migration.downgrade()
                migration.upgrade()
            assert compare_metadata(context,Base.metadata)==[]
        await conn.run_sync(roundtrip)
    async with AsyncSession(detail_engine,expire_on_commit=False) as db:
        await create_opening_package(db,company_id=1,created_by=1,data=await payload(db))
        await db.commit()
    async with detail_engine.begin() as conn:
        def forbidden(sync):
            with Operations.context(MigrationContext.configure(sync)):
                with pytest.raises(RuntimeError,match='destroy'):
                    migration.downgrade()
        await conn.run_sync(forbidden)
        assert await conn.scalar(text('SELECT count(*) FROM opening_balance_details'))==1
        assert await conn.scalar(text('SELECT count(*) FROM counterparty_open_items'))==2


async def test_http_package_attachment_permissions_and_response(detail_engine):
    from fastapi import FastAPI
    from httpx import ASGITransport,AsyncClient
    from app.api.deps import get_current_user
    from app.api.v1.opening_balances import router
    from app.core.database import get_db
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.user_company_role import UserCompanyRole
    from app.models.rbac import role_permissions
    async with AsyncSession(detail_engine,expire_on_commit=False) as db:
        db.add(Role(id=1,name='Detail accountant'));await db.flush()
        for index,action in enumerate(('create','post','read'),1):
            db.add(Permission(id=index,name='journal_entries.'+action))
        await db.flush()
        await db.execute(role_permissions.insert(),[dict(role_id=1,permission_id=i) for i in (1,2,3)])
        db.add(UserCompanyRole(user_id=1,company_id=1,role_id=1))
        data=await payload(db)
        await db.commit()
    app=FastAPI();app.include_router(router)
    async def database():
        async with AsyncSession(detail_engine,expire_on_commit=False) as db:
            yield db
    app.dependency_overrides[get_db]=database
    app.dependency_overrides[get_current_user]=lambda:User(id=1)
    base='/companies/1/opening-balances'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        denied=await client.post('/companies/2/opening-balances/packages/create',json=data.model_dump(mode='json'))
        assert denied.status_code==403
        created=await client.post(base+'/packages/create',json=data.model_dump(mode='json'))
        assert created.status_code==201,created.text
        oid=created.json()['id']
        assert created.json()['journal_status']=='posted'
        assert len(created.json()['detail']['debts'])==2
        read=await client.get(f'{base}/{oid}')
        assert read.status_code==200 and len(read.json()['detail']['stock'])==1
        retry=await client.post(f'{base}/{oid}/details',json=data.details.model_dump(mode='json'))
        assert retry.status_code==200
        async with AsyncSession(detail_engine) as db:
            await db.execute(text('DELETE FROM role_permissions WHERE permission_id=2'));await db.commit()
        assert (await client.post(f'{base}/{oid}/details',json=data.details.model_dump(mode='json'))).status_code==403
