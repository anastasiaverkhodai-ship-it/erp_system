"""Acceptance across opening, posting, reporting, periods and year-end closing."""
import os
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from test_opening_balances_postgresql import opening_engine
from test_year_end_closing_postgresql import year_engine, config, request, post_operations, YEAR, END
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.schemas.opening_balance import OpeningBalanceCreate
from app.services.opening_balance_service import create_opening_balance, post_opening_balance, reverse_opening_balance
from app.services.accounting_posting import post_journal_entry, AccountingPostingError
from app.services.accounting_reversal import reverse_journal_entry
from app.services.general_ledger_service import get_general_ledger, get_account_card
from app.services.trial_balance_service import get_trial_balance
from app.services.year_end_closing_service import preview_year_end, close_year, reverse_year_end
from app.services.accounting_control_service import get_consolidated_accounting_controls
from app.api.v1.accounting_periods import close_accounting_period
from app.api.v1.journal_entries import update_journal_entry
from app.schemas.journal_entry import JournalEntryUpdate

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv("RUN_POSTGRES_E2E")!="1",reason="Set RUN_POSTGRES_E2E=1")]


async def draft(engine):
    async with AsyncSession(engine,expire_on_commit=False) as db:
        journal=JournalEntry(company_id=1,entry_date=date(2026,8,1),created_by=1,status="draft")
        journal.lines=[JournalEntryLine(line_no=1,account_id=1,debit=100,credit=0),
                       JournalEntryLine(line_no=2,account_id=2,debit=0,credit=100)]
        db.add(journal);await db.commit()
        return journal.id


async def test_post_validates_fresh_lines_after_another_session_edits_draft(opening_engine):
    jid=await draft(opening_engine)
    async with AsyncSession(opening_engine,expire_on_commit=False) as reader:
        cached=await reader.scalar(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(JournalEntry.id==jid))
        assert sum(l.credit for l in cached.lines)==100
        async with AsyncSession(opening_engine) as writer:
            await writer.execute(text("UPDATE journal_entry_lines SET credit=101 WHERE journal_entry_id=:id AND credit>0"),{"id":jid})
            await writer.commit()
        with pytest.raises(AccountingPostingError,match="not balanced"):
            await post_journal_entry(reader,1,jid)
        assert await reader.scalar(text("SELECT status FROM journal_entries WHERE id=:id"),{"id":jid})=="draft"


async def test_reversal_refreshes_status_and_lines_from_completed_post(opening_engine):
    jid=await draft(opening_engine)
    async with AsyncSession(opening_engine,expire_on_commit=False) as reader:
        cached=await reader.scalar(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(JournalEntry.id==jid))
        assert cached.status=="draft"
        async with AsyncSession(opening_engine) as writer:
            await writer.execute(text("UPDATE journal_entry_lines SET debit=debit*1.1,credit=credit*1.1 WHERE journal_entry_id=:id"),{"id":jid})
            await post_journal_entry(writer,1,jid)
            await writer.commit()
        reversal=await reverse_journal_entry(reader,1,jid,date(2026,8,2),1)
        assert sum(l.debit for l in reversal.lines)==sum(l.credit for l in reversal.lines)==110
        report=await get_trial_balance(reader,company_id=1,date_from=date(2026,8,1),date_to=date(2026,8,2))
        assert report.total_closing_debit==report.total_closing_credit==0


async def test_cached_draft_cannot_be_edited_after_concurrent_post(opening_engine):
    jid=await draft(opening_engine)
    async with AsyncSession(opening_engine,expire_on_commit=False) as reader:
        cached=await reader.get(JournalEntry,jid)
        assert cached.status=="draft"
        async with AsyncSession(opening_engine) as writer:
            await post_journal_entry(writer,1,jid);await writer.commit()
        with pytest.raises(HTTPException) as caught:
            await update_journal_entry(1,jid,JournalEntryUpdate(description="forbidden change"),db=reader)
        assert caught.value.status_code==409
        assert await reader.scalar(text("SELECT description FROM journal_entries WHERE id=:id"),{"id":jid}) is None


async def test_period_transition_uses_current_locked_state(opening_engine):
    async with AsyncSession(opening_engine,expire_on_commit=False) as reader:
        cached=await reader.scalar(select(AccountingPeriod).where(AccountingPeriod.company_id==1))
        pid=cached.id
        async with AsyncSession(opening_engine) as writer:
            await close_accounting_period(1,pid,db=writer)
        with pytest.raises(HTTPException) as caught:
            await close_accounting_period(1,pid,db=reader)
        assert caught.value.status_code==409
        assert cached.status=="closed" and cached.is_locked


async def test_opening_operations_reports_year_end_and_reversals_agree(year_engine):
    async with AsyncSession(year_engine,expire_on_commit=False) as db:
        opening=await create_opening_balance(db,1,1,OpeningBalanceCreate(
            request_key="acceptance-opening",opening_date=date(YEAR,12,1),
            lines=[dict(account_id=1,debit="1000"),dict(account_id=2,credit="1000")]))
        await post_opening_balance(db,1,opening.id)
        await post_operations(db)
        card=await get_account_card(db,company_id=1,account_id=1,date_from=date(YEAR,12,2),date_to=END)
        assert card.opening_balance==1000 and card.closing_balance==1040
        assert card.period_debit==100 and card.period_credit==60
        preview=await preview_year_end(db,1,YEAR,config())
        closing=await close_year(db,1,YEAR,1,request(preview))
        for start,end in [(date(YEAR,12,1),END),(date(YEAR+1,1,1),date(YEAR+1,12,31))]:
            trial=await get_trial_balance(db,company_id=1,date_from=start,date_to=end)
            ledger=await get_general_ledger(db,company_id=1,date_from=start,date_to=end)
            assert ledger.period_debit==trial.total_period_debit
            assert ledger.period_credit==trial.total_period_credit
            assert ledger.closing_balance==0
            assert trial.total_closing_debit==trial.total_closing_credit==1040
            for line in trial.lines:
                account_card=await get_account_card(db,company_id=1,account_id=line.account_id,date_from=start,date_to=end)
                assert account_card.opening_balance==line.opening_debit-line.opening_credit
                assert account_card.closing_balance==line.closing_debit-line.closing_credit
                assert account_card.period_debit==line.period_debit
                assert account_card.period_credit==line.period_credit
        await reverse_year_end(db,1,closing.id,1)
        await reverse_opening_balance(db,1,opening.id,END,1)
        historical=await get_account_card(db,company_id=1,account_id=1,date_from=date(YEAR,12,1),date_to=date(YEAR,12,30))
        assert historical.closing_balance==1040
        revised=await preview_year_end(db,1,YEAR,config())
        assert revised.profit==40
        await close_year(db,1,YEAR,1,request(revised,key="acceptance-reclose"))
        trial=await get_trial_balance(db,company_id=1,date_from=date(YEAR+1,1,1),date_to=date(YEAR+1,12,31))
        assert trial.total_opening_debit==trial.total_opening_credit==40
        foreign=await get_general_ledger(db,company_id=2,date_from=date(YEAR,1,1),date_to=END)
        assert foreign.lines==[] and foreign.period_debit==foreign.period_credit==0


async def test_reports_and_incomplete_controls_work_in_read_only_transaction(year_engine):
    async with AsyncSession(year_engine) as db:
        db.add_all([
            Account(id=20,company_id=1,code="641",name="VAT",account_type="liability",normal_balance="debit_credit",is_system=True),
            Account(id=21,company_id=1,code="643",name="VAT bridge",account_type="liability",normal_balance="debit_credit",is_system=True),
        ])
        (await db.get(Account,10)).is_system=True
        await post_operations(db);await db.commit()
    async with AsyncSession(year_engine) as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        ledger=await get_general_ledger(db,company_id=1,date_from=date(YEAR,1,1),date_to=END)
        trial=await get_trial_balance(db,company_id=1,date_from=date(YEAR,1,1),date_to=END)
        card=await get_account_card(db,company_id=1,account_id=1,date_from=date(YEAR,1,1),date_to=END)
        controls=await get_consolidated_accounting_controls(db,company_id=1,date_from=date(YEAR,1,1),date_to=END)
        assert ledger.period_debit==trial.total_period_debit==160
        assert card.closing_balance==40
        assert not controls.matched and not controls.coverage_complete
        assert controls.checked_families_matched and controls.status=="incomplete"
        assert controls.not_implemented_family_count==5
        assert await db.scalar(text("SHOW transaction_read_only"))=="on"


async def test_reports_refresh_cached_journal_and_account_metadata(opening_engine):
    jid=await draft(opening_engine)
    async with AsyncSession(opening_engine,expire_on_commit=False) as reader:
        cached_journal=await reader.get(JournalEntry,jid)
        cached_account=await reader.get(Account,1)
        assert cached_journal.entry_date==date(2026,8,1)
        async with AsyncSession(opening_engine) as writer:
            journal=await writer.get(JournalEntry,jid)
            journal.entry_date=date(2026,8,2)
            (await writer.get(Account,1)).name="Updated cash name"
            await post_journal_entry(writer,1,jid);await writer.commit()
        report=await get_general_ledger(reader,company_id=1,date_from=date(2026,8,2),date_to=date(2026,8,2))
        assert len(report.lines)==2
        assert all(line.entry_date==date(2026,8,2) for line in report.lines)
        assert next(line for line in report.lines if line.account_id==1).account_name=="Updated cash name"
        card=await get_account_card(reader,company_id=1,account_id=1,date_from=date(2026,8,2),date_to=date(2026,8,2))
        assert card.account_name=="Updated cash name" and card.closing_balance==100


async def test_reporting_http_permissions_and_read_only_contract(year_engine):
    from fastapi import FastAPI
    from httpx import ASGITransport,AsyncClient
    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.models.user import User
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.user_company_role import UserCompanyRole
    from app.models.rbac import role_permissions
    from app.api.v1.general_ledger import router as ledger_router
    from app.api.v1.trial_balance import router as trial_router
    from app.api.v1.accounting_controls import router as controls_router
    async with AsyncSession(year_engine) as db:
        db.add(Role(id=1,name="Accounting reader"))
        db.add(Permission(id=1,name="journal_entries.read"))
        db.add_all([
            Account(id=20,company_id=1,code="641",name="VAT",account_type="liability",normal_balance="debit_credit",is_system=True),
            Account(id=21,company_id=1,code="643",name="VAT bridge",account_type="liability",normal_balance="debit_credit",is_system=True),
        ])
        (await db.get(Account,10)).is_system=True
        await db.flush()
        await db.execute(role_permissions.insert().values(role_id=1,permission_id=1))
        db.add(UserCompanyRole(user_id=1,company_id=1,role_id=1))
        await post_operations(db);await db.commit()
    app=FastAPI()
    for router in (ledger_router,trial_router,controls_router):
        app.include_router(router)
    async def database():
        async with AsyncSession(year_engine) as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            yield db
    app.dependency_overrides[get_db]=database
    app.dependency_overrides[get_current_user]=lambda:User(id=1)
    query="?date_from=2025-01-01&date_to=2025-12-31"
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        for endpoint in ("general-ledger","trial-balance","accounts/1/card","accounting-controls"):
            response=await client.get("/companies/1/"+endpoint+query)
            assert response.status_code==200,response.text
            if endpoint=="accounting-controls":
                assert response.json()["status"]=="incomplete" and not response.json()["matched"]
            denied=await client.get("/companies/2/"+endpoint+query)
            assert denied.status_code==403
        for endpoint in ("general-ledger","trial-balance","accounting-controls"):
            invalid=await client.get("/companies/1/"+endpoint+"?date_from=2025-12-31&date_to=2025-01-01")
            assert invalid.status_code==422
        assert (await client.get("/companies/1/accounts/3/card"+query)).status_code==404
