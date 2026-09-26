import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

import asyncio
import os
import pytest_asyncio
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
import app.models
from app.core.database import Base
from app.core.config import settings
from app.services.accounting_reversal import reverse_journal_entry, AccountingReversalError
from app.services.fixed_asset_acquisition_service import reverse_fixed_asset_acquisition_cost, FixedAssetAcquisitionError
from app.services.fixed_asset_service import update_fixed_asset, FixedAssetValidationError
from app.schemas.fixed_asset import FixedAssetUpdate

from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.company import Company
from app.models.fixed_asset import (
    FixedAsset,
    FixedAssetStatus,
)
from app.models.fixed_asset_acquisition import (
    FixedAssetAcquisitionCost,
)
from app.models.fixed_asset_commissioning import (
    FixedAssetCommissioning,
)
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import JournalEntryLine
from app.models.user import User
from app.models import FixedAssetGroup
from app.services.fixed_asset_commissioning_service import (
    FixedAssetCommissioningError,
    create_and_post_commissioning,
    reverse_commissioning,
)


def _column_value(model, name, fallback):
    column = model.__table__.columns.get(name)

    if column is None:
        return None

    if column.default is not None:
        return None

    if column.server_default is not None:
        return None

    if column.nullable:
        return None

    return fallback


def _minimal_kwargs(model, explicit):
    values = dict(explicit)

    for column in model.__table__.columns:
        if column.name in values:
            continue

        if column.primary_key:
            continue

        if column.nullable:
            continue

        if column.default is not None:
            continue

        if column.server_default is not None:
            continue

        if column.foreign_keys:
            continue

        python_type = None

        try:
            python_type = column.type.python_type
        except Exception:
            pass

        if python_type is str:
            values[column.name] = (
                f"test-{column.name}-{uuid.uuid4().hex[:8]}"
            )
        elif python_type is bool:
            values[column.name] = True
        elif python_type is int:
            values[column.name] = 1
        elif python_type is Decimal:
            values[column.name] = Decimal("0.00")
        elif python_type is date:
            values[column.name] = date(2026, 1, 1)
        else:
            raise RuntimeError(
                f"Cannot infer required field "
                f"{model.__name__}.{column.name} "
                f"of type {column.type}"
            )

    return values



pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_POSTGRES_E2E") != "1", reason="Set RUN_POSTGRES_E2E=1")]

@pytest_asyncio.fixture
async def engine():
    admin = create_async_engine(settings.database_url, poolclass=NullPool)
    schema = "test_fa_comm_" + uuid.uuid4().hex
    engine = create_async_engine(settings.database_url, poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()

async def seed(db):
    suffix = uuid.uuid4().hex[:10]

    company = Company(
        **_minimal_kwargs(
            Company,
            {
                "name": f"FA Commissioning {suffix}",
            },
        )
    )

    db.add(company)
    await db.flush()

    actor = User(
        **_minimal_kwargs(
            User,
            {
                "email": f"fa-commission-{suffix}@example.test",
                "password_hash": "test-password-hash",
                "first_name": "Fixed",
                "last_name": "Asset",
            },
        )
    )

    db.add(actor)
    await db.flush()

    account_common = {
        "company_id": company.id,
        "account_type": "asset",
        "normal_balance": "debit",
        "is_active": True,
        "is_postable": True,
    }

    source_account = Account(
        **_minimal_kwargs(
            Account,
            {
                **account_common,
                "code": f"15{suffix[:4]}",
                "name": f"Capital investment {suffix}",
            },
        )
    )

    asset_account = Account(
        **_minimal_kwargs(
            Account,
            {
                **account_common,
                "code": f"10{suffix[:4]}",
                "name": f"Fixed assets {suffix}",
            },
        )
    )

    depreciation_account = Account(
        **_minimal_kwargs(
            Account,
            {
                **account_common,
                "code": f"13{suffix[:4]}",
                "name": f"Accumulated depreciation {suffix}",
            },
        )
    )

    expense_account = Account(
        **_minimal_kwargs(
            Account,
            {
                **account_common,
                "code": f"92{suffix[:4]}",
                "name": f"Depreciation expense {suffix}",
            },
        )
    )

    db.add_all(
        [
            source_account,
            asset_account,
            depreciation_account,
            expense_account,
        ]
    )
    await db.flush()

    period = AccountingPeriod(
        **_minimal_kwargs(
            AccountingPeriod,
            {
                "company_id": company.id,
                "year": 2026,
                "month": 1,
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 1, 31),
                "status": "open",
                "is_locked": False,
            },
        )
    )

    db.add(period)

    february_period = AccountingPeriod(
        company_id=company.id,
        year=2026,
        month=2,
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 28),
        status="open",
        is_locked=False,
    )

    db.add(february_period)
    await db.flush()

    source_journal = JournalEntry(
        **_minimal_kwargs(
            JournalEntry,
            {
                "company_id": company.id,
                "entry_date": date(2026, 1, 10),
                "description": (
                    f"FA acquisition source {suffix}"
                ),
                "status": JournalEntryStatus.POSTED,
                    "created_by": actor.id,
            },
        )
    )

    db.add(source_journal)
    await db.flush()

    source_line = JournalEntryLine(
        **_minimal_kwargs(
            JournalEntryLine,
            {
                "journal_entry_id": source_journal.id,
                    "line_no": 1,
                "account_id": source_account.id,
                "debit": Decimal("120000.00"),
                "credit": Decimal("0.00"),
            },
        )
    )

    balancing_line = JournalEntryLine(
        **_minimal_kwargs(
            JournalEntryLine,
            {
                "journal_entry_id": source_journal.id,
                    "line_no": 2,
                "account_id": expense_account.id,
                "debit": Decimal("0.00"),
                "credit": Decimal("120000.00"),
            },
        )
    )

    db.add_all(
        [
            source_line,
            balancing_line,
        ]
    )
    await db.flush()

    asset_group = FixedAssetGroup(
        company_id=company.id,
        code=f"FA-GRP-{suffix}",
        name=f"Fixed asset group {suffix}",
    )

    db.add(asset_group)
    await db.flush()

    asset_kwargs = {
        "company_id": company.id,
        "asset_group_id": asset_group.id,
        "asset_number": f"FA-{suffix}",
        "name": f"Commissioning asset {suffix}",
        "status": FixedAssetStatus.READY_FOR_COMMISSIONING,
        "acquisition_date": date(2026, 1, 10),
        "original_cost": Decimal("120000.00"),
        "salvage_value": Decimal("0.00"),
        "useful_life_months": 60,
        "depreciation_method": "straight_line",
        "asset_account_id": asset_account.id,
        "accumulated_depreciation_account_id":
            depreciation_account.id,
        "depreciation_expense_account_id":
            expense_account.id,
        "created_by": actor.id,
    }

    asset = FixedAsset(
        **_minimal_kwargs(
            FixedAsset,
            asset_kwargs,
        )
    )

    db.add(asset)
    await db.flush()

    acquisition = FixedAssetAcquisitionCost(
        **_minimal_kwargs(
            FixedAssetAcquisitionCost,
            {
                "company_id": company.id,
                "fixed_asset_id": asset.id,
                "source_journal_entry_line_id":
                    source_line.id,
                "recognition_date": date(2026, 1, 10),
                "cost_type": "acquisition",
                "amount": Decimal("120000.00"),
                "request_key":
                    f"fa-acq-{suffix}",
                "created_by": actor.id,
            },
        )
    )

    db.add(acquisition)
    await db.flush()

    await db.commit()
    return SimpleNamespace(company=company.id, actor=actor.id, asset=asset.id,
        source=source_journal.id, cost=acquisition.id, source_account=source_account.id,
        asset_account=asset_account.id, depreciation_account=depreciation_account.id)

async def commission(db, f, key="commission", day=date(2026,2,1)):
    return await create_and_post_commissioning(db, company_id=f.company,
        fixed_asset_id=f.asset, request_key=key, commissioning_date=day, created_by=f.actor)

async def reverse(db, f, cid, day=date(2026,2,2)):
    return await reverse_commissioning(db, company_id=f.company, fixed_asset_id=f.asset,
        commissioning_id=cid, reversal_date=day, reversed_by=f.actor)

async def test_lifecycle_retry_and_generic_bypass(engine):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f=await seed(db)
        c=await commission(db,f)
        assert (await commission(db,f)).id==c.id
        journal=await db.scalar(select(JournalEntry).where(JournalEntry.fixed_asset_commissioning_id==c.id))
        assert journal.status==JournalEntryStatus.POSTED
        lines=(await db.scalars(select(JournalEntryLine).where(JournalEntryLine.journal_entry_id==journal.id))).all()
        assert {(l.account_id,l.debit,l.credit) for l in lines}=={
            (f.asset_account,Decimal('120000'),Decimal(0)),(f.source_account,Decimal(0),Decimal('120000'))}
        with pytest.raises(AccountingReversalError,match="lifecycle"):
            await reverse_journal_entry(db,f.company,journal.id,date(2026,2,2),f.actor)
        with pytest.raises(AccountingReversalError,match="allocations"):
            await reverse_journal_entry(db,f.company,f.source,date(2026,2,2),f.actor)
        with pytest.raises(FixedAssetAcquisitionError,match="commissioning"):
            await reverse_fixed_asset_acquisition_cost(db,company_id=f.company,fixed_asset_id=f.asset,
                acquisition_cost_id=f.cost,reversal_date=date(2026,2,2),reversed_by=f.actor)
        with pytest.raises(FixedAssetValidationError):
            await update_fixed_asset(db,f.company,f.asset,FixedAssetUpdate(status='draft',effective_date=date(2026,2,2)),f.actor)
        r=await reverse(db,f,c.id)
        assert (await reverse(db,f,c.id)).id==r.id
        asset=await db.get(FixedAsset,f.asset)
        assert asset.status==FixedAssetStatus.READY_FOR_COMMISSIONING and asset.in_service_date is None
        reversal_journal=await db.scalar(select(JournalEntry).where(JournalEntry.fixed_asset_commissioning_id==r.id))
        assert reversal_journal.reversal_of_id==journal.id
        with pytest.raises(FixedAssetCommissioningError,match="latest"):
            await commission(db,f,'earlier',date(2026,2,1))
        await commission(db,f,'again',date(2026,2,3))
        assert asset.status==FixedAssetStatus.IN_SERVICE

@pytest.mark.parametrize('case',['draft','closed','future','source_reversed','cost_mismatch','inactive_account','foreign_company','empty_key','reserved_key','early_date'])
async def test_invalid_inputs_and_atomic_rollback(engine,case):
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db)
        asset=await db.get(FixedAsset,f.asset)
        key='key';day=date(2026,2,1)
        if case=='draft':asset.status=FixedAssetStatus.DRAFT
        if case=='closed':await db.execute(text("UPDATE accounting_periods SET status='closed',is_locked=true"))
        if case=='future':day=date(2099,1,1)
        if case=='source_reversed':(await db.get(JournalEntry,f.source)).status=JournalEntryStatus.REVERSED
        if case=='cost_mismatch':asset.original_cost=1
        if case=='inactive_account':(await db.get(Account,f.depreciation_account)).is_active=False
        if case=='foreign_company':f.company+=999
        if case=='empty_key':key='   '
        if case=='reserved_key':key='reversal:1'
        if case=='early_date':day=date(2026,1,1)
        await db.commit()
        with pytest.raises((FixedAssetCommissioningError,FixedAssetValidationError,FixedAssetAcquisitionError,HTTPException)):
            await commission(db,f,key,day)
        await db.rollback()
        assert await db.scalar(text('SELECT count(*) FROM fixed_asset_commissionings'))==0
        assert await db.scalar(text('SELECT count(*) FROM journal_entries'))==1

async def test_concurrent_retries_and_conflicts(engine):
    async with AsyncSession(engine,expire_on_commit=False) as db:f=await seed(db)
    async def work(key='same'):
        async with AsyncSession(engine,expire_on_commit=False) as db:
            c=await commission(db,f,key);await db.commit();return c.id
    ids=await asyncio.wait_for(asyncio.gather(work(),work()),30)
    assert ids[0]==ids[1]
    async with AsyncSession(engine,expire_on_commit=False) as db:
        with pytest.raises(FixedAssetCommissioningError):await commission(db,f,'different')
        with pytest.raises(FixedAssetCommissioningError,match='different request'):
            await commission(db,f,'same',date(2026,2,2))
    async def undo():
        async with AsyncSession(engine,expire_on_commit=False) as db:
            r=await reverse(db,f,ids[0]);await db.commit();return r.id
    ids=await asyncio.wait_for(asyncio.gather(undo(),undo()),30)
    assert ids[0]==ids[1]

async def test_reversal_period_and_date_guards(engine):
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db);c=await commission(db,f);await db.commit();cid=c.id
        for day in [date(2026,1,31),date(2099,1,1)]:
            with pytest.raises(FixedAssetCommissioningError):await reverse(db,f,cid,day)
        await db.execute(text("UPDATE accounting_periods SET status='closed',is_locked=true"));await db.commit()
        with pytest.raises(HTTPException):await reverse(db,f,cid)
        await db.rollback()
        assert await db.scalar(text('SELECT count(*) FROM fixed_asset_commissionings'))==1

async def test_http_permissions_and_rollback(engine):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.api.v1.fixed_asset_commissionings import router
    from app.api.v1.fixed_asset_acquisitions import router as acquisition_router
    from app.api.v1.fixed_assets import router as cards_router
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.user_company_role import UserCompanyRole
    from app.models.rbac import role_permissions
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db)
        role=Role(name='FA accountant');db.add(role);await db.flush()
        for action in ['read','create','update','post','reverse']:
            permission=Permission(name='journal_entries.'+action);db.add(permission);await db.flush()
            await db.execute(role_permissions.insert().values(role_id=role.id,permission_id=permission.id))
        db.add(UserCompanyRole(user_id=f.actor,company_id=f.company,role_id=role.id));await db.commit()
    app=FastAPI()
    for r in (router,acquisition_router,cards_router):app.include_router(r)
    async def database():
        async with AsyncSession(engine,expire_on_commit=False) as db:yield db
    app.dependency_overrides[get_db]=database
    app.dependency_overrides[get_current_user]=lambda:User(id=f.actor)
    base=f'/companies/{f.company}/fixed-assets/{f.asset}'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        body={'request_key':'http','commissioning_date':'2026-02-01'}
        assert (await client.post('/companies/999/fixed-assets/1/commissionings',json=body)).status_code==403
        assert (await client.get('/companies/999/fixed-assets')).status_code==403
        assert (await client.get('/companies/999/fixed-assets/1/acquisition-costs')).status_code==403
        assert (await client.post(base+'/commissionings',json={**body,'request_key':'  '})).status_code==422
        made=await client.post(base+'/commissionings',json=body)
        assert made.status_code==201,made.text
        cid=made.json()['id']
        card=await client.get(base)
        assert card.status_code==200 and card.json()['status']=='in_service'
        history=await client.get(base+'/commissionings')
        assert history.status_code==200 and history.json()[0]['id']==cid
        assert (await client.patch(base,json={'status':'draft','effective_date':'2026-02-02'})).status_code==400
        denied_source=await client.post(base+f'/acquisition-costs/{f.cost}/reverse',json={'reversal_date':'2026-02-02'})
        assert denied_source.status_code==400,denied_source.text
        undone=await client.post(base+f'/commissionings/{cid}/reverse',json={'reversal_date':'2026-02-02'})
        assert undone.status_code==200,undone.text
        # The acquisition reversal route used to omit fixed_asset_id and fail with TypeError.
        assert (await client.post(base+f'/acquisition-costs/{f.cost}/reverse',json={'reversal_date':'2026-02-02'})).status_code==200
        async with AsyncSession(engine) as db:
            await db.execute(text("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE name='journal_entries.post')"));await db.commit()
        assert (await client.post(base+'/commissionings',json=body)).status_code==403

async def test_failure_after_journal_creation_rolls_back(engine,monkeypatch):
    from app.api.v1.fixed_asset_commissionings import post_fixed_asset_commissioning
    from app.schemas.fixed_asset_commissioning import FixedAssetCommissioningCreate
    from app.services.accounting_posting import AccountingPostingError
    import app.services.fixed_asset_commissioning_service as service
    async def fail(*args,**kwargs):raise AccountingPostingError('injected posting failure')
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db)
        monkeypatch.setattr(service,'post_journal_entry',fail)
        with pytest.raises(HTTPException) as caught:
            await post_fixed_asset_commissioning(f.company,f.asset,
                FixedAssetCommissioningCreate(request_key='fail',commissioning_date=date(2026,2,1)),
                db=db,current_user=User(id=f.actor))
        assert caught.value.status_code==400
        assert 'fixed_asset_commissioning' not in db.info
        assert await db.scalar(text('SELECT count(*) FROM fixed_asset_commissionings'))==0
        assert await db.scalar(text('SELECT count(*) FROM journal_entries'))==1
        assert (await db.get(FixedAsset,f.asset)).status==FixedAssetStatus.READY_FOR_COMMISSIONING

async def test_database_source_exclusivity_and_tenant_fk(engine):
    from sqlalchemy.exc import IntegrityError
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db);c=await commission(db,f)
        jid=await db.scalar(select(JournalEntry.id).where(JournalEntry.fixed_asset_commissioning_id==c.id))
        with pytest.raises(IntegrityError,match='ck_je_commissioning_exclusive'):
            async with db.begin_nested():
                await db.execute(text('UPDATE journal_entries SET document_id=999 WHERE id=:id'),{'id':jid})
        other=Company(name='Other');db.add(other);await db.flush()
        with pytest.raises(IntegrityError,match='fk_facomm_company_asset'):
            async with db.begin_nested():
                db.add(FixedAssetCommissioning(company_id=other.id,fixed_asset_id=f.asset,
                    request_key='foreign',commissioning_date=date(2026,2,1),created_by=f.actor))
                await db.flush()

async def test_migrations_upgrade_downgrade_preserve_existing_journal(engine):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    def load(filename):
        spec=importlib.util.spec_from_file_location('fa_migration',Path('alembic/versions')/filename)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    initial=load('760e17482bce_add_fixed_asset_commissioning_lifecycle.py')
    hardening=load('8e124c09a671_harden_fixed_asset_commissioning_source.py')
    async with AsyncSession(engine,expire_on_commit=False) as db:f=await seed(db)
    async with engine.begin() as conn:
        def exercise(sync):
            with Operations.context(MigrationContext.configure(sync)):
                hardening.downgrade();initial.downgrade()
                initial.upgrade();hardening.upgrade()
        await conn.run_sync(exercise)
        assert await conn.scalar(text('SELECT count(*) FROM journal_entries'))==1
        assert await conn.scalar(text('SELECT count(*) FROM journal_entry_lines'))==2
        assert await conn.scalar(text("SELECT count(*) FROM pg_constraint WHERE conrelid='journal_entries'::regclass AND conname='ck_je_commissioning_exclusive'"))==1
