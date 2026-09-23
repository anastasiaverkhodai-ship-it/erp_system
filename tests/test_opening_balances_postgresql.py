"""Real PostgreSQL lifecycle, reporting, retry and tenant boundary tests."""
import asyncio
import os
from datetime import date, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.models
from app.core.config import settings
from app.core.database import Base
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.company import Company
from app.models.journal_entry import JournalEntry
from app.models.opening_balance import OpeningBalance
from app.models.user import User
from app.schemas.opening_balance import OpeningBalanceCreate
from app.services.opening_balance_service import (
    OpeningBalanceError, OpeningBalanceNotFoundError, create_opening_balance,
    get_opening_balance, post_opening_balance, reverse_opening_balance,
)
from app.services.general_ledger_service import get_account_card, get_general_ledger
from app.services.trial_balance_service import get_trial_balance
from app.services.accounting_reversal import reverse_journal_entry, AccountingReversalError

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_E2E") != "1", reason="Set RUN_POSTGRES_E2E=1")]
D1, D2, D3 = date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)


@pytest_asyncio.fixture
async def opening_engine():
    # Committed isolated schema allows independent concurrent transactions.
    admin = create_async_engine(settings.database_url, poolclass=NullPool)
    schema = "test_opening_" + uuid4().hex
    engine = create_async_engine(settings.database_url, poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add_all([Company(id=1, name="Opening fixture"), Company(id=2, name="Other"),
                User(id=1, email="opening@example.test", password_hash="unused",
                     first_name="Test", last_name="User")])
            await db.flush()
            db.add_all([
                Account(id=1, company_id=1, code="301", name="Cash", account_type="asset", normal_balance="debit"),
                Account(id=2, company_id=1, code="401", name="Capital", account_type="equity", normal_balance="credit"),
                Account(id=3, company_id=2, code="301", name="Foreign", account_type="asset", normal_balance="debit"),
                Account(id=4, company_id=1, code="30", name="Parent", account_type="asset", normal_balance="debit", is_postable=False),
                Account(id=5, company_id=1, code="302", name="Inactive", account_type="asset", normal_balance="debit", is_active=False),
                AccountingPeriod(company_id=1, year=2026, month=8, start_date=D1,
                    end_date=date(2026, 8, 31), status="open", is_locked=False)])
            await db.commit()
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


def payload(key="opening-1", **overrides):
    data = dict(request_key=key, opening_date=D1,
        lines=[dict(account_id=1, debit="100.00"), dict(account_id=2, credit="100.00")])
    data.update(overrides)
    return OpeningBalanceCreate(**data)


async def test_lifecycle_reports_retries_and_reversal_provenance(opening_engine):
    async with AsyncSession(opening_engine, expire_on_commit=False) as db:
        opening = await create_opening_balance(db, 1, 1, payload())
        oid, jid = opening.id, opening.journal_entry_id
        assert (await create_opening_balance(db, 1, 1, payload())).id == oid
        with pytest.raises(OpeningBalanceError, match="different"):
            await create_opening_balance(db, 1, 1, payload(description="changed"))
        draft = await get_trial_balance(db, company_id=1, date_from=D1, date_to=D3)
        assert draft.total_period_debit == draft.total_period_credit == 0
        assert (await post_opening_balance(db, 1, oid)).id == jid
        assert (await post_opening_balance(db, 1, oid)).id == jid
        posted = await get_trial_balance(db, company_id=1, date_from=D1, date_to=D3)
        assert posted.total_period_debit == posted.total_period_credit == 100
        assert posted.total_closing_debit == posted.total_closing_credit == 100
        card = await get_account_card(db, company_id=1, account_id=1, date_from=D2, date_to=D3)
        assert card.opening_balance == card.closing_balance == 100
        ledger = await get_general_ledger(db, company_id=1, date_from=D1, date_to=D3)
        assert len(ledger.lines) == 2
        reverse = await reverse_opening_balance(db, 1, oid, D3, 1)
        assert reverse.opening_balance_id == oid and reverse.reversal_of_id == jid
        assert (await reverse_opening_balance(db, 1, oid, D3, 1)).id == reverse.id
        with pytest.raises(OpeningBalanceError, match="another date"):
            await reverse_opening_balance(db, 1, oid, D2, 1)
        historical = await get_account_card(db, company_id=1, account_id=1, date_from=D1, date_to=D2)
        assert historical.closing_balance == 100
        final = await get_trial_balance(db, company_id=1, date_from=D2, date_to=D3)
        assert final.total_opening_debit == final.total_opening_credit == 100
        assert final.total_period_debit == final.total_period_credit == 100
        assert final.total_closing_debit == final.total_closing_credit == 0
        assert await db.scalar(text("SELECT count(*) FROM journal_entries")) == 2
        assert await db.scalar(text("SELECT count(*) FROM journal_entry_lines")) == 4
        with pytest.raises(OpeningBalanceNotFoundError):
            await get_opening_balance(db, 2, oid)
        other = await get_trial_balance(db, company_id=2, date_from=D1, date_to=D3)
        assert other.total_period_debit == 0


@pytest.mark.parametrize("account_id", [3, 4, 5, 999])
async def test_invalid_accounts_leave_no_journal(opening_engine, account_id):
    async with AsyncSession(opening_engine) as db:
        data = payload(lines=[dict(account_id=account_id, debit=100), dict(account_id=2, credit=100)])
        with pytest.raises(OpeningBalanceError, match="account"):
            await create_opening_balance(db, 1, 1, data)
        assert await db.scalar(text("SELECT count(*) FROM journal_entries")) == 0


async def test_dates_balance_and_closed_periods(opening_engine):
    async with AsyncSession(opening_engine, expire_on_commit=False) as db:
        with pytest.raises(OpeningBalanceError, match="Future"):
            await create_opening_balance(db, 1, 1, payload(opening_date=date.today()+timedelta(days=1)))
        with pytest.raises(OpeningBalanceError, match="not balanced"):
            await create_opening_balance(db, 1, 1, payload(lines=[dict(account_id=1,debit=100),dict(account_id=2,credit=99)]))
        opening = await create_opening_balance(db, 1, 1, payload())
        oid, jid = opening.id, opening.journal_entry_id
        await db.execute(text("UPDATE accounting_periods SET status='closed', is_locked=true"))
        with pytest.raises(HTTPException) as caught:
            await post_opening_balance(db, 1, oid)
        assert caught.value.status_code == 409
        assert await db.scalar(text("SELECT status FROM journal_entries WHERE id=:id"), {"id":jid}) == "draft"
        await db.execute(text("UPDATE accounting_periods SET status='open', is_locked=false"))
        await post_opening_balance(db, 1, oid)
        for invalid_date in [D1-timedelta(days=1), date.today()+timedelta(days=1)]:
            with pytest.raises(OpeningBalanceError):
                await reverse_opening_balance(db, 1, oid, invalid_date, 1)
        await db.execute(text("UPDATE accounting_periods SET status='closed', is_locked=true"))
        with pytest.raises(HTTPException):
            await reverse_opening_balance(db, 1, oid, D3, 1)
        assert await db.scalar(text("SELECT count(*) FROM journal_entries")) == 1
        await db.execute(text("UPDATE accounting_periods SET status='open', is_locked=false"))
        # Generic reversal also preserves provenance and cannot precede the opening.
        with pytest.raises(AccountingReversalError):
            await reverse_journal_entry(db, 1, jid, D1-timedelta(days=1), 1)
        reverse = await reverse_journal_entry(db, 1, jid, D3, 1)
        assert reverse.opening_balance_id == oid
        assert (await reverse_opening_balance(db, 1, oid, D3, 1)).id == reverse.id


async def test_company_foreign_key_and_unique_request_identity(opening_engine):
    async with AsyncSession(opening_engine, expire_on_commit=False) as db:
        opening = await create_opening_balance(db, 1, 1, payload())
        # Use an unlinked journal so the tenant FK, not journal uniqueness, fails.
        journal = JournalEntry(company_id=1, entry_date=D1, created_by=1, status="draft")
        db.add(journal)
        await db.flush()
        jid = journal.id
        with pytest.raises(IntegrityError) as foreign:
            async with db.begin_nested():
                await db.execute(text("INSERT INTO opening_balances (company_id,opening_date,journal_entry_id,created_by,created_at,request_key,request_fingerprint) VALUES (2,:d,:j,1,now(),'foreign',repeat('0',64))"), {"d":D1,"j":jid})
        assert "fk_opening_company_journal" in str(foreign.value)
        with pytest.raises(IntegrityError) as duplicate:
            async with db.begin_nested():
                await db.execute(text("INSERT INTO opening_balances (company_id,opening_date,journal_entry_id,created_by,created_at,request_key,request_fingerprint) VALUES (1,:d,:j,1,now(),'opening-1',repeat('0',64))"), {"d":D1,"j":jid})
        assert "uq_opening_company_request" in str(duplicate.value)
        assert await db.scalar(text("SELECT count(*) FROM opening_balances")) == 1


async def test_concurrent_create_post_and_reverse_are_exactly_once(opening_engine):
    async def create():
        async with AsyncSession(opening_engine, expire_on_commit=False) as db:
            result = await create_opening_balance(db, 1, 1, payload())
            await db.commit()
            return result.id
    ids = await asyncio.wait_for(asyncio.gather(create(), create()), timeout=20)
    assert ids[0] == ids[1]
    async def post():
        async with AsyncSession(opening_engine, expire_on_commit=False) as db:
            result = await post_opening_balance(db, 1, ids[0])
            await db.commit()
            return result.id
    posted = await asyncio.wait_for(asyncio.gather(post(), post()), timeout=20)
    assert posted[0] == posted[1]
    async def reverse():
        async with AsyncSession(opening_engine, expire_on_commit=False) as db:
            result = await reverse_opening_balance(db, 1, ids[0], D3, 1)
            await db.commit()
            return result.id
    reversed_ids = await asyncio.wait_for(asyncio.gather(reverse(), reverse()), timeout=20)
    assert reversed_ids[0] == reversed_ids[1]
    async with AsyncSession(opening_engine) as db:
        assert await db.scalar(text("SELECT count(*) FROM opening_balances")) == 1
        assert await db.scalar(text("SELECT count(*) FROM journal_entries")) == 2


async def test_migration_roundtrip_and_legacy_backfill(opening_engine):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    def import_module(name):
        path = Path("alembic/versions") / (name.rsplit(".",1)[-1] + ".py")
        spec = spec_from_file_location(path.stem, path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    foundation = import_module("alembic.versions.af229fa18ec8_add_opening_balance_provenance")
    hardening = import_module("alembic.versions.bf330ab29fd9_harden_opening_balance_identity")
    year_end = import_module("alembic.versions.c0441bc30ae0_add_year_end_closing")
    async with opening_engine.begin() as conn:
        def migrate(sync):
            with Operations.context(MigrationContext.configure(sync)):
                year_end.downgrade()
                hardening.downgrade()
                foundation.downgrade()
                foundation.upgrade()
                # Simulate a real legacy record before applying additive hardening.
                sync.execute(text("INSERT INTO journal_entries (id,company_id,entry_date,status,created_by,created_at) VALUES (99,1,'2026-08-01','draft',1,now())"))
                sync.execute(text("INSERT INTO opening_balances (id,company_id,opening_date,journal_entry_id,created_by,created_at) VALUES (99,1,'2026-08-01',99,1,now())"))
                sync.execute(text("UPDATE journal_entries SET opening_balance_id=99 WHERE id=99"))
                hardening.upgrade()
                year_end.upgrade()
        await conn.run_sync(migrate)
        row = (await conn.execute(text("SELECT request_key, request_fingerprint, journal_entry_id FROM opening_balances WHERE id=99"))).one()
        assert row == ("legacy:99", "0"*64, 99)
        assert await conn.scalar(text("SELECT opening_balance_id FROM journal_entries WHERE id=99")) == 99


async def test_http_lifecycle_real_permissions_and_tenant_isolation(opening_engine):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.api.deps import get_current_user
    from app.api.v1.opening_balances import router
    from app.core.database import get_db
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.user_company_role import UserCompanyRole
    from app.models.rbac import role_permissions
    async with AsyncSession(opening_engine) as db:
        role = Role(id=1, name="Opening accountant")
        db.add(role)
        await db.flush()
        for index, action in enumerate(["create", "read", "post", "reverse"], 1):
            db.add(Permission(id=index, name="journal_entries."+action))
        await db.flush()
        await db.execute(role_permissions.insert(), [dict(role_id=1,permission_id=i) for i in range(1,5)])
        db.add(UserCompanyRole(user_id=1, company_id=1, role_id=1))
        await db.commit()
    app = FastAPI()
    app.include_router(router)
    async def database():
        async with AsyncSession(opening_engine, expire_on_commit=False) as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: User(id=1)
    base = "/companies/1/opening-balances"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post("/companies/2/opening-balances",json=payload().model_dump(mode="json"))
        assert denied.status_code == 403
        created = await client.post(base,json=payload().model_dump(mode="json"))
        assert created.status_code == 201, created.text
        oid = created.json()["id"]
        assert created.json()["journal_status"] == "draft"
        conflict = await client.post(base,json=payload(description="different").model_dump(mode="json"))
        assert conflict.status_code == 409
        posted = await client.post(f"{base}/{oid}/post")
        assert posted.status_code == 200 and posted.json()["journal_status"] == "posted"
        reversed_result = await client.post(f"{base}/{oid}/reverse",json={"reversal_date":D3.isoformat()})
        assert reversed_result.status_code == 200 and reversed_result.json()["journal_status"] == "reversed"
        assert reversed_result.json()["reversal_journal_entry_id"] is not None
        read = await client.get(f"{base}/{oid}")
        assert read.status_code == 200 and len(read.json()["lines"]) == 2
        missing = await client.get(f"{base}/999")
        assert missing.status_code == 404
        async with AsyncSession(opening_engine) as db:
            await db.execute(text("DELETE FROM role_permissions WHERE permission_id=4"))
            await db.commit()
        assert (await client.post(f"{base}/{oid}/reverse",json={"reversal_date":D3.isoformat()})).status_code == 403
