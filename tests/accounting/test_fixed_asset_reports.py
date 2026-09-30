"""12.11 reports and 12.12 end-to-end accounting closure, isolated PostgreSQL."""
import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from test_fixed_asset_commissioning import engine, seed as commission_seed, commission, reverse
from test_fixed_asset_disposal_postgresql import seed
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.fixed_asset import FixedAsset, FixedAssetStatus
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.schemas.opening_balance_detail import OpeningPackageCreate
from app.schemas.fixed_asset_disposal import FixedAssetDisposalCreate
from app.schemas.fixed_asset_repair_improvement import FixedAssetRepairImprovementCreate
from app.schemas.fixed_asset_revaluation_impairment import FixedAssetRevaluationImpairmentCreate
from app.services.opening_balance_detail_service import create_opening_package
from app.services.opening_balance_service import reverse_opening_balance
from app.services.fixed_asset_depreciation_service import create_and_post_depreciation, reverse_depreciation
from app.services.fixed_asset_disposal_service import create_fixed_asset_disposal, reverse_fixed_asset_disposal
from app.services.fixed_asset_repair_improvement_service import create_fixed_asset_repair_improvement, reverse_fixed_asset_repair_improvement
from app.services.fixed_asset_revaluation_impairment_service import create_fixed_asset_revaluation_impairment, reverse_fixed_asset_revaluation_impairment
from app.services.fixed_asset_report_service import get_fixed_asset_movement, get_fixed_asset_gl_reconciliation, get_fixed_asset_depreciation_report
from app.services.trial_balance_service import get_trial_balance

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='Set RUN_POSTGRES_E2E=1')]
D = Decimal

def day(n):
    return date(2026, 2, n)


async def report(db, f, start=1, end=28):
    return await get_fixed_asset_movement(db, company_id=f.company, date_from=day(start), date_to=day(end))


async def opening(db):
    f = await seed(db)
    a = await db.get(FixedAsset, f.asset)
    a.status, a.in_service_date = FixedAssetStatus.DRAFT, None
    for code in ['281', '361', '631']:
        db.add(Account(company_id=f.company, code=code, name=code, account_type='asset', normal_balance='debit', is_active=True, is_postable=True, is_system=True))
    await db.flush()
    data = OpeningPackageCreate.model_validate({'opening': {'request_key': 'open', 'opening_date': str(day(1)),
        'lines': [{'account_id': f.asset_account, 'debit': '10000'}, {'account_id': f.accumulated_account, 'credit': '1000'},
                  {'account_id': f.counterpart_account, 'credit': '9000'}]},
        'details': {'fixed_assets': [{'fixed_asset_id': f.asset, 'acquisition_date': '2026-01-01',
            'in_service_date': '2026-01-15', 'original_cost': '10000', 'accumulated_depreciation': '1000'}]}})
    o = await create_opening_package(db, company_id=f.company, created_by=f.actor, data=data)
    return f, o


async def test_commissioning_history_survives_future_reversal_and_card_edits(engine):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f = await commission_seed(db)
        c = await commission(db, f)
        assert c.cost_snapshot[0]['amount'] == '120000.00'
        await reverse(db, f, c.id, day(10))
        a = await db.get(FixedAsset, f.asset)
        a.original_cost = D('999')  # report must never derive the past from the card
        before = await report(db, f, end=9)
        assert before.reconciled, before.issues
        assert before.totals.closing_cost == 120000
        after = await report(db, f, start=10)
        assert after.reconciled, after.issues
        assert after.totals.opening_cost == 120000
        assert after.totals.cost_decrease == 120000
        assert after.totals.closing_cost == 0


async def test_opening_depreciation_partial_sale_reversal_and_trial_balance(engine):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f, o = await opening(db)
        dep = await create_and_post_depreciation(db=db, company_id=f.company, fixed_asset_id=f.asset,
            request_key='dep', period_start=day(1), period_end=day(28), posting_date=day(28), created_by=f.actor)
        disp = await create_fixed_asset_disposal(db, f.company, f.asset, FixedAssetDisposalCreate(
            disposal_date=day(28), disposal_account_id=f.disposal_account, disposal_fraction=D('.25'), request_key='partial'), f.actor)
        result = await report(db, f)
        assert result.reconciled, result.issues
        assert result.totals.closing_cost == 7500
        assert result.totals.closing_accumulated == (D('1000')+dep.amount)*D('.75')
        gl = await get_fixed_asset_gl_reconciliation(db, company_id=f.company, date_from=day(1), date_to=day(28))
        assert gl.matched, gl.issues
        tb = await get_trial_balance(db, company_id=f.company, date_from=day(1), date_to=day(28))
        for row in gl.lines:
            t = next(t for t in tb.lines if t.account_id == row.account_id)
            assert row.actual.closing == t.closing_debit-t.closing_credit
            assert row.actual.period_debit == t.period_debit
            assert row.actual.period_credit == t.period_credit
        await reverse_fixed_asset_disposal(db, f.company, f.asset, disp.id, day(28), 'undo-dispose', f.actor)
        await reverse_depreciation(db=db, company_id=f.company, depreciation_id=dep.id, reversal_date=day(28), reversed_by=f.actor)
        await reverse_opening_balance(db, f.company, o.id, day(28), f.actor)
        closed = await report(db, f)
        assert closed.reconciled, closed.issues
        assert closed.totals.closing_cost == closed.totals.closing_accumulated == 0
        statement = await get_fixed_asset_depreciation_report(db, company_id=f.company, date_from=day(1), date_to=day(28))
        assert statement.charged == statement.reversed == dep.amount
        assert statement.net == 0
        past = await report(db, f, end=27)
        assert past.reconciled, past.issues
        assert past.totals.closing_cost == 10000
        assert past.totals.closing_accumulated == 1000


async def test_improvement_valuation_and_reversals_have_independent_amounts(engine):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f, _ = await opening(db)
        source = JournalEntry(company_id=f.company, entry_date=day(2), status='posted', created_by=f.actor,
            lines=[JournalEntryLine(line_no=1, account_id=f.depreciation_expense_account, debit=500, credit=0),
                   JournalEntryLine(line_no=2, account_id=f.counterpart_account, debit=0, credit=500)])
        db.add(source)
        await db.flush()
        imp = await create_fixed_asset_repair_improvement(db, f.company, f.asset, FixedAssetRepairImprovementCreate(
            operation_type='improvement', operation_date=day(3), amount=500, source_journal_entry_line_id=source.lines[0].id, request_key='improve'), f.actor)
        val = await create_fixed_asset_revaluation_impairment(db, f.company, f.asset, FixedAssetRevaluationImpairmentCreate(
            operation_type='revaluation', operation_date=day(4), carrying_amount_after=12000,
            counterpart_account_id=f.counterpart_account, request_key='value'), f.actor)
        assert (await report(db, f, end=4)).totals.closing_cost == 13000
        await reverse_fixed_asset_revaluation_impairment(db, f.company, f.asset, val.id, day(5), 'undo-value', f.actor)
        await reverse_fixed_asset_repair_improvement(db, f.company, f.asset, imp.id, day(6), 'undo-improve', f.actor)
        r = await report(db, f)
        assert r.reconciled, r.issues
        assert r.totals.closing_cost == 10000
        assert r.totals.cost_increase == 13000
        assert r.totals.cost_decrease == 3000
        assert (await report(db, f, end=4)).totals.closing_net == 12000


@pytest.mark.parametrize('damage', ['amount', 'date', 'draft', 'extra', 'manual_offset'])
async def test_reconciliation_detects_damage_even_if_net_balance_matches(engine, damage):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f = await commission_seed(db)
        c = await commission(db, f)
        j = await db.scalar(select(JournalEntry).where(JournalEntry.fixed_asset_commissioning_id == c.id))
        lines = list((await db.scalars(select(JournalEntryLine).where(JournalEntryLine.journal_entry_id == j.id))).all())
        if damage == 'amount':
            for line in lines:
                if line.debit: line.debit += 1
                if line.credit: line.credit += 1
        elif damage == 'date': j.entry_date = day(2)
        elif damage == 'draft': j.status = JournalEntryStatus.DRAFT
        elif damage == 'extra':
            db.add(JournalEntryLine(journal_entry_id=j.id, line_no=3, account_id=f.asset_account, debit=2, credit=0))
            db.add(JournalEntryLine(journal_entry_id=j.id, line_no=4, account_id=f.asset_account, debit=0, credit=2))
        else:
            db.add(JournalEntry(company_id=f.company, entry_date=day(3), status='posted', created_by=f.actor,
                lines=[JournalEntryLine(line_no=1, account_id=f.asset_account, debit=8, credit=0),
                       JournalEntryLine(line_no=2, account_id=f.asset_account, debit=0, credit=8)]))
        await db.flush()
        r = await get_fixed_asset_gl_reconciliation(db, company_id=f.company, date_from=day(1), date_to=day(28))
        assert not r.matched
        assert r.issues
        if damage in ('extra', 'manual_offset'):
            assert all(row.difference.closing == 0 for row in r.lines)
            assert any(row.difference.period_debit != 0 for row in r.lines)


async def test_tenant_scope_invalid_dates_and_unbacked_cards(engine):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f = await commission_seed(db)
        await commission(db, f)
        other = await commission_seed(db)
        r = await report(db, other)
        assert r.reconciled
        assert r.totals.closing_cost == 0
        assert all(e.fixed_asset_id != f.asset for e in r.events)
        with pytest.raises(ValueError, match='company'):
            await get_fixed_asset_movement(db, company_id=other.company, date_from=day(1), date_to=day(28), fixed_asset_id=f.asset)
        with pytest.raises(ValueError, match='date_from'):
            await report(db, f, start=28, end=1)
        unbacked = await seed(db)
        r = await report(db, unbacked)
        assert not r.reconciled
        assert 'missing_asset_basis' in {i.code for i in r.issues}


async def test_zero_production_has_no_false_missing_journal(engine):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f, _ = await opening(db)
        a = await db.get(FixedAsset, f.asset)
        a.depreciation_method = 'production'
        a.expected_output = D('1000')
        await db.flush()
        await create_and_post_depreciation(db=db, company_id=f.company, fixed_asset_id=f.asset, request_key='idle',
            period_start=day(1), period_end=day(28), posting_date=day(28), created_by=f.actor, actual_output=D('0'))
        r = await get_fixed_asset_depreciation_report(db, company_id=f.company, date_from=day(1), date_to=day(28))
        assert r.reconciled, r.issues
        assert r.net == 0
        assert len(r.events) == 1
        assert r.events[0].actual_output == 0
        assert r.events[0].journal_entry_ids == []


async def test_http_reports_require_read_permission_and_do_not_write(engine):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text
    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.api.v1.fixed_asset_reports import router, get_report_db
    from app.models.user import User
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.user_company_role import UserCompanyRole
    from app.models.rbac import role_permissions
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f = await commission_seed(db)
        await commission(db, f)
        role = Role(name='FA report reader')
        permission = Permission(name='journal_entries.read')
        db.add_all([role, permission])
        await db.flush()
        await db.execute(role_permissions.insert().values(role_id=role.id, permission_id=permission.id))
        db.add(UserCompanyRole(user_id=f.actor, company_id=f.company, role_id=role.id))
        await db.commit()
        before = (await db.execute(text('SELECT to_jsonb(t) FROM journal_entries t ORDER BY id'))).scalars().all()
    app = FastAPI()
    app.include_router(router)
    async def database():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_report_db] = database
    app.dependency_overrides[get_current_user] = lambda: User(id=f.actor)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        for suffix in ('movement', 'depreciation', 'gl-reconciliation'):
            path = f'/companies/{f.company}/fixed-asset-reports/{suffix}'
            response = await client.get(path, params={'date_from': '2026-02-01', 'date_to': '2026-02-28'})
            assert response.status_code == 200, response.text
            assert (await client.get(path, params={'date_from': '2026-02-28', 'date_to': '2026-02-01'})).status_code == 422
            assert (await client.get(f'/companies/999999/fixed-asset-reports/{suffix}', params={'date_from': '2026-02-01', 'date_to': '2026-02-28'})).status_code == 403
        async with AsyncSession(engine) as db:
            assert (await db.execute(text('SELECT to_jsonb(t) FROM journal_entries t ORDER BY id'))).scalars().all() == before
            await db.execute(role_permissions.delete())
            await db.commit()
        assert (await client.get(path, params={'date_from': '2026-02-01', 'date_to': '2026-02-28'})).status_code == 403


async def test_legacy_commissioning_and_source_snapshot_are_audited(engine):
    from app.models.fixed_asset_acquisition import FixedAssetAcquisitionCost
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f = await commission_seed(db)
        c = await commission(db, f)
        c.cost_snapshot = None
        await db.flush()
        assert (await report(db, f)).reconciled  # safe recovery from independent cost history
        cost = await db.get(FixedAssetAcquisitionCost, f.cost)
        cost.amount = D('119999')
        await db.flush()
        result = await report(db, f)
        assert not result.reconciled
        assert 'journal_amounts' in {i.code for i in result.issues}


async def test_full_sale_leaves_no_asset_balance_and_does_not_duplicate_income(engine):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f, _ = await opening(db)
        income = Account(company_id=f.company, code='712', name='Sale income', account_type='income', normal_balance='credit', is_active=True, is_postable=True)
        debtor = Account(company_id=f.company, code='377', name='Buyer', account_type='asset', normal_balance='debit', is_active=True, is_postable=True)
        db.add_all([income, debtor])
        await db.flush()
        sale = JournalEntry(company_id=f.company, entry_date=day(2), status='draft', created_by=f.actor,
            lines=[JournalEntryLine(line_no=1, account_id=debtor.id, debit=11000, credit=0),
                   JournalEntryLine(line_no=2, account_id=income.id, debit=0, credit=11000)])
        db.add(sale)
        await db.flush()
        from app.services.accounting_posting import post_journal_entry
        await post_journal_entry(db, f.company, sale.id)
        disp = await create_fixed_asset_disposal(db, f.company, f.asset, FixedAssetDisposalCreate(
            disposal_date=day(3), disposal_account_id=f.disposal_account, sale_source_line_id=sale.lines[1].id, request_key='sale'), f.actor)
        r = await report(db, f)
        assert r.reconciled, r.issues
        assert r.totals.closing_cost == r.totals.closing_accumulated == r.totals.closing_net == 0
        assert next(e for e in r.events if e.event_type == 'disposal').sale_net_amount == 11000
        assert (await db.get(FixedAsset, f.asset)).original_cost == 10000  # retained master is not book balance
        await reverse_fixed_asset_disposal(db, f.company, f.asset, disp.id, day(4), 'undo-sale', f.actor)
        r = await report(db, f)
        assert r.reconciled, r.issues
        assert r.totals.closing_net == 9000
        tb = await get_trial_balance(db, company_id=f.company, date_from=day(1), date_to=day(28))
        assert next(t for t in tb.lines if t.account_id == income.id).period_credit == 11000


async def test_snapshot_migration_preserves_data_and_refuses_history_loss(engine):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import text
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path('alembic/versions/8fa12512c001_commissioning_report_sources.py')
    spec = importlib.util.spec_from_file_location('snapshot_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f = await commission_seed(db)
    async with engine.begin() as conn:
        before = (await conn.execute(text('SELECT to_jsonb(t) FROM journal_entries t ORDER BY id'))).scalars().all()
        def roundtrip(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
                migration.upgrade()
        await conn.run_sync(roundtrip)
        assert (await conn.execute(text('SELECT to_jsonb(t) FROM journal_entries t ORDER BY id'))).scalars().all() == before
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await commission(db, f)
        await db.commit()
    async with engine.begin() as conn:
        def refuse(sync):
            with Operations.context(MigrationContext.configure(sync)):
                with pytest.raises(RuntimeError, match='history'):
                    migration.downgrade()
        await conn.run_sync(refuse)
        assert await conn.scalar(text('SELECT count(*) FROM fixed_asset_commissionings WHERE cost_snapshot IS NOT NULL')) == 1


async def test_http_report_transaction_is_read_only_repeatable_snapshot(engine, monkeypatch):
    from contextlib import aclosing
    from sqlalchemy import text
    import app.api.v1.fixed_asset_reports as api
    monkeypatch.setattr(api, 'engine', engine)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f = await commission_seed(db)
        await commission(db, f)
        await db.commit()
    async with aclosing(api.get_report_db()) as dependency:
        reader = await anext(dependency)
        assert await reader.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
        assert await reader.scalar(text('SHOW transaction_read_only')) == 'on'
        first = await report(reader, f)
        assert first.reconciled
        async with AsyncSession(engine) as writer:
            j = JournalEntry(company_id=f.company, entry_date=day(3), status='posted', created_by=f.actor,
                lines=[JournalEntryLine(line_no=1, account_id=f.asset_account, debit=1, credit=0),
                       JournalEntryLine(line_no=2, account_id=f.source_account, debit=0, credit=1)])
            writer.add(j)
            await writer.commit()
        second = await report(reader, f)
        assert second == first
    async with aclosing(api.get_report_db()) as dependency:
        fresh = await report(await anext(dependency), f)
        assert not fresh.reconciled
        assert 'unexplained_journal' in {i.code for i in fresh.issues}
