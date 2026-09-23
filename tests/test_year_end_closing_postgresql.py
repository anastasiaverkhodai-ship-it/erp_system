"""Year-end behavior on isolated PostgreSQL schemas, including real contention."""
import asyncio
import calendar
import os
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from test_opening_balances_postgresql import opening_engine
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.year_end_closing import YearEndClosing
from app.schemas.year_end_closing import YearEndPreviewRequest, YearEndCloseRequest
from app.services.year_end_closing_service import (
    YearEndError, YearEndNotFound, preview_year_end, close_year, reverse_year_end, get_closing,
)
from app.services.accounting_posting import post_journal_entry, AccountingPostingError
from app.services.accounting_reversal import reverse_journal_entry, AccountingReversalError
from app.services.trial_balance_service import get_trial_balance
from app.api.v1.accounting_periods import reopen_accounting_period, create_accounting_period
from app.schemas.accounting_period import AccountingPeriodCreate

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_POSTGRES_E2E") != "1", reason="Set RUN_POSTGRES_E2E=1")]
YEAR = 2025
END = date(YEAR,12,31)


def config(**overrides):
    data = dict(profit_account_id=13,loss_account_id=14,
        mappings=[dict(source_account_id=10,result_account_id=12),dict(source_account_id=11,result_account_id=12)])
    data.update(overrides)
    return YearEndPreviewRequest(**data)


def request(preview, key="close-2025", **overrides):
    values = dict(**config().model_dump(),request_key=key,preview_fingerprint=preview.preview_fingerprint)
    values.update(overrides)
    return YearEndCloseRequest(**values)


async def post_operations(db, income=100, expense=60, year=YEAR):
    journal = JournalEntry(company_id=1,entry_date=date(year,12,30),created_by=1,status="draft")
    journal.lines = [
        JournalEntryLine(line_no=1,account_id=1,debit=income,credit=0),
        JournalEntryLine(line_no=2,account_id=10,debit=0,credit=income),
        JournalEntryLine(line_no=3,account_id=11,debit=expense,credit=0),
        JournalEntryLine(line_no=4,account_id=1,debit=0,credit=expense),
    ]
    db.add(journal)
    await db.flush()
    await post_journal_entry(db,1,journal.id)
    return journal


@pytest_asyncio.fixture
async def year_engine(opening_engine):
    async with AsyncSession(opening_engine) as db:
        db.add_all([
            Account(id=10,company_id=1,code="702",name="Revenue",account_type="income",normal_balance="credit"),
            Account(id=11,company_id=1,code="902",name="COGS",account_type="expense",normal_balance="debit"),
            Account(id=12,company_id=1,code="791",name="Financial result",account_type="equity",normal_balance="debit_credit"),
            Account(id=13,company_id=1,code="441",name="Retained profit",account_type="equity",normal_balance="credit"),
            Account(id=14,company_id=1,code="442",name="Uncovered loss",account_type="equity",normal_balance="debit"),
        ])
        for month in range(1,13):
            db.add(AccountingPeriod(company_id=1,year=YEAR,month=month,start_date=date(YEAR,month,1),
                end_date=date(YEAR,month,calendar.monthrange(YEAR,month)[1]),status="open" if month==12 else "closed",is_locked=month!=12))
        await db.commit()
    yield opening_engine


@pytest.mark.parametrize("income,expense,profit", [(100,60,40),(60,100,-40),(100,100,0)])
async def test_reports_close_reverse_reclose_and_retries(year_engine,income,expense,profit):
    async with AsyncSession(year_engine,expire_on_commit=False) as db:
        await post_operations(db,income,expense)
        preview = await preview_year_end(db,1,YEAR,config())
        assert preview.profit == profit
        assert sum(l.debit for l in preview.lines) == sum(l.credit for l in preview.lines)
        closing = await close_year(db,1,YEAR,1,request(preview))
        cid,jid = closing.id,closing.journal_entry_id
        assert (await close_year(db,1,YEAR,1,request(preview))).id == cid
        with pytest.raises(YearEndError,match="different"):
            await close_year(db,1,YEAR,1,request(preview,preview_fingerprint="0"*64))
        report = await get_trial_balance(db,company_id=1,date_from=date(2026,1,1),date_to=date(2026,12,31))
        rows = {r.account_id:r for r in report.lines}
        for aid in (10,11,12):
            assert rows[aid].opening_debit == rows[aid].opening_credit == 0
        assert report.total_opening_debit == report.total_opening_credit == abs(profit)
        if profit > 0:
            assert rows[13].opening_credit == profit
        if profit < 0:
            assert rows[14].opening_debit == -profit
        with pytest.raises(AccountingReversalError,match="lifecycle"):
            await reverse_journal_entry(db,1,jid,END,1)
        december = await db.scalar(select(AccountingPeriod).where(AccountingPeriod.company_id==1,AccountingPeriod.year==YEAR,AccountingPeriod.month==12))
        assert december.is_locked
        with pytest.raises(HTTPException,match="closed"):
            await reopen_accounting_period(1,december.id,db=db)
        with pytest.raises(HTTPException):
            await create_accounting_period(1,AccountingPeriodCreate(year=2024,month=1),db=db)
        await reverse_year_end(db,1,cid,1)
        assert not december.is_locked and closing.status == "reversed"
        assert (await reverse_year_end(db,1,cid,1)).id == cid
        reverse = await db.scalar(select(JournalEntry).where(JournalEntry.reversal_of_id==jid))
        assert reverse.year_end_closing_id == cid and reverse.entry_date == END
        revised = await preview_year_end(db,1,YEAR,config())
        assert revised.profit == profit
        replacement = await close_year(db,1,YEAR,1,request(revised,key="replacement"))
        assert replacement.id != cid
        assert await db.scalar(text("SELECT count(*) FROM year_end_closings WHERE status='closed'")) == 1
        with pytest.raises(YearEndNotFound):
            await get_closing(db,2,cid)


async def test_empty_year_closes_without_zero_journal(year_engine):
    async with AsyncSession(year_engine,expire_on_commit=False) as db:
        preview = await preview_year_end(db,1,YEAR,config())
        assert preview.lines == [] and preview.profit == 0
        closing = await close_year(db,1,YEAR,1,request(preview))
        assert closing.journal_entry_id is None
        assert await db.scalar(text("SELECT count(*) FROM journal_entries")) == 0
        await reverse_year_end(db,1,closing.id,1)
        assert closing.status == "reversed"


async def test_stale_preview_and_unresolved_drafts(year_engine):
    async with AsyncSession(year_engine,expire_on_commit=False) as db:
        await post_operations(db)
        preview = await preview_year_end(db,1,YEAR,config())
        await post_operations(db,income=10,expense=5)
        with pytest.raises(YearEndError,match="stale"):
            await close_year(db,1,YEAR,1,request(preview))
        draft = JournalEntry(company_id=1,entry_date=END,created_by=1,status="draft")
        db.add(draft); await db.flush()
        fresh = await preview_year_end(db,1,YEAR,config())
        with pytest.raises(YearEndError,match="draft"):
            await close_year(db,1,YEAR,1,request(fresh))
        assert await db.scalar(text("SELECT count(*) FROM year_end_closings")) == 0


async def test_period_and_account_preconditions(year_engine):
    async with AsyncSession(year_engine,expire_on_commit=False) as db:
        await post_operations(db)
        with pytest.raises(YearEndError,match="Missing"):
            await preview_year_end(db,1,YEAR,config(mappings=[]))
        with pytest.raises(YearEndError,match="foreign"):
            await preview_year_end(db,1,YEAR,config(profit_account_id=3))
        with pytest.raises(YearEndError,match="44"):
            await preview_year_end(db,1,YEAR,config(profit_account_id=12))
        preview = await preview_year_end(db,1,YEAR,config())
        period = await db.scalar(select(AccountingPeriod).where(AccountingPeriod.company_id==1,AccountingPeriod.year==YEAR,AccountingPeriod.month==1))
        period.status="open"; period.is_locked=False
        await db.flush()
        with pytest.raises(YearEndError,match="earlier periods"):
            await close_year(db,1,YEAR,1,request(preview))
        period.status="closed"; period.is_locked=True
        await db.delete(period); await db.flush()
        with pytest.raises(YearEndError,match="twelve"):
            await close_year(db,1,YEAR,1,request(preview))
        with pytest.raises(YearEndError,match="completed"):
            await close_year(db,1,date.today().year,1,request(preview))


async def test_concurrent_closings_and_reversals_are_single_operations(year_engine):
    async with AsyncSession(year_engine) as db:
        await post_operations(db)
        preview = await preview_year_end(db,1,YEAR,config())
        await db.commit()
    async def close():
        async with AsyncSession(year_engine,expire_on_commit=False) as db:
            closing = await close_year(db,1,YEAR,1,request(preview))
            await db.commit()
            return closing.id
    ids = await asyncio.wait_for(asyncio.gather(close(),close()),20)
    assert ids[0] == ids[1]
    async def reverse():
        async with AsyncSession(year_engine,expire_on_commit=False) as db:
            closing = await reverse_year_end(db,1,ids[0],1)
            await db.commit()
            return closing.id
    assert await asyncio.wait_for(asyncio.gather(reverse(),reverse()),20) == ids
    async with AsyncSession(year_engine) as db:
        assert await db.scalar(text("SELECT count(*) FROM year_end_closings")) == 1
        assert await db.scalar(text("SELECT count(*) FROM journal_entries WHERE year_end_closing_id IS NOT NULL")) == 2


async def test_year_end_migration_roundtrip(year_engine):
    from importlib.util import module_from_spec,spec_from_file_location
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.autogenerate import compare_metadata
    from app.core.database import Base
    spec=spec_from_file_location("year_end_revision","alembic/versions/c0441bc30ae0_add_year_end_closing.py")
    migration=module_from_spec(spec);spec.loader.exec_module(migration)
    async with year_engine.begin() as conn:
        def roundtrip(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
                migration.upgrade()
            assert compare_metadata(MigrationContext.configure(sync),Base.metadata) == []
        await conn.run_sync(roundtrip)


async def test_prior_balances_allocation_and_financial_result_routing(year_engine):
    async with AsyncSession(year_engine,expire_on_commit=False) as db:
        await post_operations(db)
        income = await db.get(Account,10)
        income.code="73"
        await db.flush()
        with pytest.raises(YearEndError,match="792"):
            await preview_year_end(db,1,YEAR,config())
        income.code="702"
        expense = await db.get(Account,11)
        expense.code="91"
        await db.flush()
        with pytest.raises(YearEndError,match="allocation"):
            await preview_year_end(db,1,YEAR,config())
        expense.code="902"
        await db.execute(text("UPDATE journal_entries SET entry_date='2024-12-31'"))
        with pytest.raises(YearEndError,match="prior year"):
            await preview_year_end(db,1,YEAR,config())


async def test_close_blocks_waiting_post_and_new_request_key(year_engine):
    async with AsyncSession(year_engine,expire_on_commit=False) as first:
        await post_operations(first)
        preview=await preview_year_end(first,1,YEAR,config())
        await first.commit()
        closing=await close_year(first,1,YEAR,1,request(preview))
        # A concurrent ordinary journal cannot pass the December row lock.
        started=asyncio.Event()
        async def ordinary_post():
            async with AsyncSession(year_engine) as second:
                started.set()
                with pytest.raises(HTTPException,match="closed"):
                    await post_operations(second,income=10,expense=5)
                await second.rollback()
        task=asyncio.create_task(ordinary_post())
        await started.wait()
        await first.commit()
        await asyncio.wait_for(task,20)
        with pytest.raises(HTTPException,match="closed"):
            await close_year(first,1,YEAR,1,request(preview,key="another"))
        assert await first.scalar(text("SELECT count(*) FROM year_end_closings")) == 1


async def test_reversal_failure_is_atomic_and_permissions_are_real(year_engine,monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport,AsyncClient
    from app.api.deps import get_current_user
    from app.api.v1.year_end_closings import router
    from app.core.database import get_db
    from app.models.user import User
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.user_company_role import UserCompanyRole
    from app.models.rbac import role_permissions
    import app.services.year_end_closing_service as service
    from unittest.mock import AsyncMock
    async with AsyncSession(year_engine) as db:
        await post_operations(db)
        db.add(Role(id=1,name="Year-end accountant"));await db.flush()
        for i,name in enumerate(["journal_entries.read","journal_entries.post","journal_entries.reverse","accounting.periods.manage"],1):
            db.add(Permission(id=i,name=name))
        await db.flush()
        await db.execute(role_permissions.insert(),[dict(role_id=1,permission_id=i) for i in range(1,5)])
        db.add(UserCompanyRole(user_id=1,company_id=1,role_id=1))
        await db.commit()
    app=FastAPI();app.include_router(router)
    async def database():
        async with AsyncSession(year_engine,expire_on_commit=False) as db:
            yield db
    app.dependency_overrides[get_db]=database
    app.dependency_overrides[get_current_user]=lambda:User(id=1)
    base="/companies/1/year-end-closings"
    async with AsyncClient(transport=ASGITransport(app=app,raise_app_exceptions=False),base_url="http://test") as client:
        denied=await client.post("/companies/2/year-end-closings/2025/preview",json=config().model_dump())
        assert denied.status_code==403
        response=await client.post(base+"/2025/preview",json=config().model_dump())
        assert response.status_code==200,response.text
        close_data={**config().model_dump(),"request_key":"http-close","preview_fingerprint":response.json()["preview_fingerprint"]}
        response=await client.post(base+"/2025/close",json=close_data)
        assert response.status_code==201,response.text
        cid=response.json()["id"]
        with monkeypatch.context() as patch:
            patch.setattr(service,"reverse_journal_entry",AsyncMock(side_effect=RuntimeError("simulated journal failure")))
            failed=await client.post(f"{base}/{cid}/reverse")
            assert failed.status_code==500
        async with AsyncSession(year_engine) as db:
            assert await db.scalar(text("SELECT status FROM year_end_closings")) == "closed"
            assert await db.scalar(text("SELECT is_locked FROM accounting_periods WHERE company_id=1 AND year=2025 AND month=12")) is True
        assert (await client.get(f"{base}/{cid}")).json()["status"]=="closed"
        assert len((await client.get(base)).json())==1
        reversed_response=await client.post(f"{base}/{cid}/reverse")
        assert reversed_response.status_code==200,reversed_response.text
        assert reversed_response.json()["reversal_journal_entry_id"] is not None
        async with AsyncSession(year_engine) as db:
            await db.execute(text("DELETE FROM role_permissions WHERE permission_id=4"));await db.commit()
        assert (await client.post(f"{base}/{cid}/reverse")).status_code==403


async def test_later_year_must_be_reversed_first(year_engine):
    async with AsyncSession(year_engine,expire_on_commit=False) as db:
        for month in range(1,13):
            db.add(AccountingPeriod(company_id=1,year=2024,month=month,start_date=date(2024,month,1),
                end_date=date(2024,month,calendar.monthrange(2024,month)[1]),status="open" if month==12 else "closed",is_locked=month!=12))
        await db.flush()
        previous_preview=await preview_year_end(db,1,2024,config())
        previous=await close_year(db,1,2024,1,request(previous_preview,key="2024"))
        await post_operations(db)
        current_preview=await preview_year_end(db,1,YEAR,config())
        current=await close_year(db,1,YEAR,1,request(current_preview))
        with pytest.raises(YearEndError,match="later"):
            await reverse_year_end(db,1,previous.id,1)
        await reverse_year_end(db,1,current.id,1)
        await reverse_year_end(db,1,previous.id,1)
        assert previous.status==current.status=="reversed"


@pytest.mark.parametrize("simplified,deduction,expected", [(False,150,-100),(True,10,40)])
async def test_contra_revenue_and_existing_result_balances(year_engine,simplified,deduction,expected):
    from app.models.company import Company
    async with AsyncSession(year_engine,expire_on_commit=False) as db:
        if simplified:
            company=await db.get(Company,1)
            company.chart_of_accounts_template="simplified_186"
            (await db.get(Account,10)).code="70"
            (await db.get(Account,11)).code="90"
            (await db.get(Account,12)).code="79"
        await post_operations(db)
        adjustment=JournalEntry(company_id=1,entry_date=END,created_by=1,status="draft")
        adjustment.lines=[
            JournalEntryLine(line_no=1,account_id=10,debit=deduction,credit=0),
            JournalEntryLine(line_no=2,account_id=1,debit=0,credit=deduction),
            JournalEntryLine(line_no=3,account_id=1,debit=10,credit=0),
            JournalEntryLine(line_no=4,account_id=12,debit=0,credit=10),
        ]
        db.add(adjustment);await db.flush()
        await post_journal_entry(db,1,adjustment.id)
        preview=await preview_year_end(db,1,YEAR,config())
        assert preview.profit==expected
        await close_year(db,1,YEAR,1,request(preview))
        report=await get_trial_balance(db,company_id=1,date_from=date(2026,1,1),date_to=date(2026,12,31))
        rows={r.account_id:r for r in report.lines}
        for aid in (10,11,12):
            assert rows[aid].opening_debit==rows[aid].opening_credit==0
        assert report.total_opening_debit==report.total_opening_credit==abs(expected)
