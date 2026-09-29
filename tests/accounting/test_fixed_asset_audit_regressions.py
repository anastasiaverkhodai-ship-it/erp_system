"""Cross-lifecycle regression cases reproduced by the 12.5–12.10 audit."""
import asyncio
import os
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from test_fixed_asset_commissioning import engine
from test_fixed_asset_disposal_postgresql import seed
from app.models.fixed_asset import FixedAsset, FixedAssetStatus
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.account import Account
from app.models.user import User
from app.services.fixed_asset_service import move_fixed_asset
from app.services.fixed_asset_depreciation_service import create_and_post_depreciation, reverse_depreciation, _posted_active_total, FixedAssetDepreciationError
from app.services.accounting_reversal import reverse_journal_entry, AccountingReversalError
from app.services.fixed_asset_disposal_service import create_fixed_asset_disposal, reverse_fixed_asset_disposal, FixedAssetDisposalError
from app.schemas.fixed_asset_disposal import FixedAssetDisposalCreate
from app.services.fixed_asset_revaluation_impairment_service import create_fixed_asset_revaluation_impairment, reverse_fixed_asset_revaluation_impairment
from app.schemas.fixed_asset_revaluation_impairment import FixedAssetRevaluationImpairmentCreate
from app.services.fixed_asset_repair_improvement_service import create_fixed_asset_repair_improvement, reverse_fixed_asset_repair_improvement, FixedAssetRepairImprovementError
from app.schemas.fixed_asset_repair_improvement import FixedAssetRepairImprovementCreate
from app.services.opening_balance_detail_service import create_opening_package
from app.services.opening_balance_service import reverse_opening_balance
from app.schemas.opening_balance_detail import OpeningPackageCreate

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')]
def day(n):return date(2026,2,n)
async def depreciation(db,f,key='dep',start=1,end=28):
    return await create_and_post_depreciation(db=db,company_id=f.company,fixed_asset_id=f.asset,
        request_key=key,period_start=day(start),period_end=day(end),posting_date=day(28),created_by=f.actor)
async def disposal(db,f,when=28):
    return await create_fixed_asset_disposal(db,f.company,f.asset,FixedAssetDisposalCreate(
        disposal_date=day(when),disposal_account_id=f.disposal_account,request_key='dispose'),f.actor)

async def test_depreciation_reversal_repost_overlap_and_concurrency(engine):
    async with AsyncSession(engine,expire_on_commit=False) as db:f=await seed(db)
    async def post():
        async with AsyncSession(engine,expire_on_commit=False) as db:
            row=await depreciation(db,f);await db.commit();return row.id
    ids=await asyncio.wait_for(asyncio.gather(post(),post()),20)
    assert ids[0]==ids[1]
    async with AsyncSession(engine,expire_on_commit=False) as db:
        with pytest.raises(FixedAssetDepreciationError):await depreciation(db,f,'overlap',15,28)
        with pytest.raises(FixedAssetDepreciationError):await depreciation(db,f,'duplicate')
        journal=await db.scalar(select(JournalEntry).where(JournalEntry.fixed_asset_depreciation_id==ids[0]))
        with pytest.raises(AccountingReversalError,match='lifecycle'):
            await reverse_journal_entry(db,f.company,journal.id,day(28),f.actor)
        r=await reverse_depreciation(db=db,company_id=f.company,fixed_asset_id=f.asset,
            depreciation_id=ids[0],reversal_date=day(28),reversed_by=f.actor)
        assert r.accumulated_after==0
        assert await _posted_active_total(db,f.company,f.asset)==0
        assert (await reverse_depreciation(db=db,company_id=f.company,depreciation_id=ids[0],
            reversal_date=day(28),reversed_by=f.actor)).id==r.id
        assert (await depreciation(db,f,'repost')).amount==150

async def test_movement_suspension_is_not_erased_by_current_status(engine):
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db)
        await move_fixed_asset(db,f.company,f.asset,effective_date=day(3),changed_by=f.actor,status=FixedAssetStatus.SUSPENDED)
        await move_fixed_asset(db,f.company,f.asset,effective_date=day(10),changed_by=f.actor,status=FixedAssetStatus.IN_SERVICE)
        with pytest.raises(FixedAssetDepreciationError,match='suspended'):
            await depreciation(db,f)
        with pytest.raises(HTTPException,match='later'):
            await move_fixed_asset(db,f.company,f.asset,effective_date=day(5),changed_by=f.actor,status=FixedAssetStatus.SUSPENDED)

async def test_disposal_chronology_and_dependent_reversals(engine):
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db);d=await depreciation(db,f)
        with pytest.raises(HTTPException):await disposal(db,f,20)
        op=await disposal(db,f)
        with pytest.raises(HTTPException):
            await reverse_depreciation(db=db,company_id=f.company,depreciation_id=d.id,reversal_date=day(28),reversed_by=f.actor)
        with pytest.raises(HTTPException):
            await reverse_fixed_asset_disposal(db,f.company,f.asset,op.id,day(1),'undo-early',f.actor)
        await reverse_fixed_asset_disposal(db,f.company,f.asset,op.id,day(28),'undo',f.actor)
        await reverse_depreciation(db=db,company_id=f.company,depreciation_id=d.id,reversal_date=day(28),reversed_by=f.actor)

async def test_valuation_must_wait_for_disposal_reversal(engine):
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db)
        v=await create_fixed_asset_revaluation_impairment(db,f.company,f.asset,FixedAssetRevaluationImpairmentCreate(
            operation_type='revaluation',operation_date=day(2),carrying_amount_after=12000,
            counterpart_account_id=f.counterpart_account,request_key='value'),f.actor)
        d=await disposal(db,f,20)
        with pytest.raises(HTTPException,match='later'):
            await reverse_fixed_asset_revaluation_impairment(db,f.company,f.asset,v.id,day(21),'undo-value',f.actor)
        await reverse_fixed_asset_disposal(db,f.company,f.asset,d.id,day(21),'undo-dispose',f.actor)
        await reverse_fixed_asset_revaluation_impairment(db,f.company,f.asset,v.id,day(21),'undo-value',f.actor)
        assert (await db.get(FixedAsset,f.asset)).original_cost==10000

async def test_improvement_source_and_missing_reversal(engine):
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db)
        j=JournalEntry(company_id=f.company,entry_date=day(1),status='posted',created_by=f.actor,
            lines=[JournalEntryLine(line_no=1,account_id=f.depreciation_expense_account,debit=500,credit=0),
                   JournalEntryLine(line_no=2,account_id=f.counterpart_account,debit=0,credit=500)])
        db.add(j);await db.flush()
        op=await create_fixed_asset_repair_improvement(db,f.company,f.asset,FixedAssetRepairImprovementCreate(
            operation_type='improvement',operation_date=day(2),amount=500,
            source_journal_entry_line_id=j.lines[0].id,request_key='improve'),f.actor)
        with pytest.raises(AccountingReversalError,match='allocations'):
            await reverse_journal_entry(db,f.company,j.id,day(3),f.actor)
        with pytest.raises(FixedAssetRepairImprovementError,match='not found'):
            await reverse_fixed_asset_repair_improvement(db,f.company,f.asset,99999,day(3),'missing',f.actor)
        await reverse_fixed_asset_repair_improvement(db,f.company,f.asset,op.id,day(3),'undo',f.actor)
        await reverse_journal_entry(db,f.company,j.id,day(3),f.actor)

async def test_opening_cannot_be_reversed_with_active_depreciation(engine):
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db);a=await db.get(FixedAsset,f.asset);a.status=FixedAssetStatus.DRAFT;a.in_service_date=None
        for code in ['281','361','631']:
            db.add(Account(company_id=f.company,code=code,name=code,account_type='asset',normal_balance='debit',is_active=True,is_postable=True,is_system=True))
        await db.flush()
        data=OpeningPackageCreate.model_validate({'opening':{'request_key':'open','opening_date':str(day(1)),
            'lines':[{'account_id':f.asset_account,'debit':'10000'},{'account_id':f.accumulated_account,'credit':'1000'},
                     {'account_id':f.counterpart_account,'credit':'9000'}]},'details':{'fixed_assets':[{'fixed_asset_id':f.asset,
                'acquisition_date':'2026-01-01','in_service_date':'2026-01-15','original_cost':'10000','accumulated_depreciation':'1000'}]}})
        o=await create_opening_package(db,company_id=f.company,created_by=f.actor,data=data)
        d=await depreciation(db,f)
        with pytest.raises(HTTPException,match='later'):
            await reverse_opening_balance(db,f.company,o.id,day(28),f.actor)
        await reverse_depreciation(db=db,company_id=f.company,depreciation_id=d.id,reversal_date=day(28),reversed_by=f.actor)
        await reverse_opening_balance(db,f.company,o.id,day(28),f.actor)
        assert a.status==FixedAssetStatus.DRAFT

async def test_depreciation_api_calls_correct_service_and_checks_asset(engine):
    from app.api.v1.fixed_asset_depreciations import post_fixed_asset_depreciation,post_fixed_asset_depreciation_reversal
    from app.schemas.fixed_asset_depreciation import FixedAssetDepreciationCreate,FixedAssetDepreciationReverse
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db);actor=User(id=f.actor)
        row=await post_fixed_asset_depreciation(f.company,f.asset,FixedAssetDepreciationCreate(
            fixed_asset_id=f.asset,request_key='api',period_start=day(1),period_end=day(28),posting_date=day(28)),db=db,current_user=actor)
        did=row.id
        with pytest.raises(HTTPException):
            await post_fixed_asset_depreciation_reversal(f.company,99999,did,FixedAssetDepreciationReverse(reversal_date=day(28)),db=db,current_user=actor)
        result=await post_fixed_asset_depreciation_reversal(f.company,f.asset,did,FixedAssetDepreciationReverse(reversal_date=day(28)),db=db,current_user=actor)
        assert result.reversal_of_id==did

async def test_manual_production_zero_output_retry_reverse_and_salvage(engine):
    from app.models.fixed_asset import FixedAssetDepreciationMethod
    from app.models.fixed_asset_depreciation import FixedAssetDepreciation
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f = await seed(db)
        asset = await db.get(FixedAsset, f.asset)
        asset.depreciation_method = FixedAssetDepreciationMethod.PRODUCTION
        await db.flush()
        from app.services.fixed_asset_service import update_fixed_asset, FixedAssetValidationError
        from app.schemas.fixed_asset import FixedAssetUpdate
        await update_fixed_asset(db,f.company,f.asset,FixedAssetUpdate(expected_output=Decimal('3000'),effective_date=day(1)),f.actor)
        with pytest.raises(FixedAssetValidationError,match='dedicated lifecycle'):
            await update_fixed_asset(db,f.company,f.asset,FixedAssetUpdate(expected_output=Decimal('6000'),effective_date=day(1)),f.actor)
        async def post(key, quantity, month=2):
            from calendar import monthrange
            return await create_and_post_depreciation(db=db, company_id=f.company,
                fixed_asset_id=f.asset, request_key=key, period_start=date(2026,month,1),
                period_end=date(2026,month,monthrange(2026,month)[1]),
                posting_date=date(2026,month,monthrange(2026,month)[1]),
                created_by=f.actor, actual_output=quantity)
        with pytest.raises(FixedAssetDepreciationError, match='requires nonnegative'):
            await post('missing', None)
        with pytest.raises(FixedAssetDepreciationError):
            await post('negative', Decimal('-1'))
        zero = await post('idle', Decimal('0'))
        assert zero.amount == 0
        assert await db.scalar(select(JournalEntry.id).where(JournalEntry.fixed_asset_depreciation_id == zero.id)) is None
        assert (await post('idle', Decimal('0'))).id == zero.id
        with pytest.raises(FixedAssetDepreciationError, match='different request'):
            await post('idle', Decimal('10'))
        await reverse_depreciation(db=db, company_id=f.company, depreciation_id=zero.id,
            reversal_date=day(28), reversed_by=f.actor)
        active = await post('actual', Decimal('100.5'))
        assert active.amount == Decimal('301.50')
        assert active.actual_output == Decimal('100.5')
        assert active.expected_output == Decimal('3000')
        assert await db.scalar(select(JournalEntry.id).where(JournalEntry.fixed_asset_depreciation_id == active.id)) is not None
        from app.models.accounting_period import AccountingPeriod
        db.add(AccountingPeriod(company_id=f.company, year=2026, month=3, start_date=date(2026,3,1), end_date=date(2026,3,31), status="open", is_locked=False))
        await db.flush()
        last = await post('finish', Decimal('3000'), 3)
        assert last.accumulated_after == Decimal('9000')
        assert last.amount == Decimal('8698.50')
        await reverse_depreciation(db=db, company_id=f.company, depreciation_id=last.id,
            reversal_date=date(2026,3,31), reversed_by=f.actor)
        assert await _posted_active_total(db,f.company,f.asset) == Decimal('301.50')

async def test_partial_sale_preserves_remaining_asset_and_never_reposts_revenue(engine):
    from app.services.fixed_asset_revaluation_impairment_service import _accumulated
    async with AsyncSession(engine, expire_on_commit=False) as db:
        f = await seed(db)
        await depreciation(db, f)
        income = Account(company_id=f.company,code='712',name='Asset sale',account_type='income',normal_balance='credit',is_active=True,is_postable=True)
        receivable = Account(company_id=f.company,code='377',name='Buyer',account_type='asset',normal_balance='debit',is_active=True,is_postable=True)
        db.add_all([income,receivable]);await db.flush()
        sale = JournalEntry(company_id=f.company,entry_date=day(28),status='draft',created_by=f.actor,
            lines=[JournalEntryLine(line_no=1,account_id=receivable.id,debit=4000,credit=0),
                   JournalEntryLine(line_no=2,account_id=income.id,debit=0,credit=4000)])
        db.add(sale);await db.flush()
        from app.services.accounting_posting import post_journal_entry
        await post_journal_entry(db,f.company,sale.id)
        data = FixedAssetDisposalCreate(disposal_date=day(28),disposal_account_id=f.disposal_account,
            disposal_fraction=Decimal('.25'), sale_source_line_id=sale.lines[1].id, request_key='partial-sale')
        row=await create_fixed_asset_disposal(db,f.company,f.asset,data,f.actor)
        assert row.original_cost == Decimal('2500')
        assert row.accumulated_depreciation == Decimal('37.50')
        assert row.sale_net_amount == Decimal('4000')
        asset=await db.get(FixedAsset,f.asset)
        assert asset.original_cost == Decimal('7500') and asset.salvage_value == Decimal('750')
        assert asset.status == FixedAssetStatus.IN_SERVICE
        assert await _accumulated(db,f.company,f.asset) == Decimal('112.50')
        lines=(await db.scalars(select(JournalEntryLine).where(JournalEntryLine.journal_entry_id==row.journal_entry_id))).all()
        assert {line.account_id for line in lines} == {asset.asset_account_id,asset.accumulated_depreciation_account_id,f.disposal_account}
        assert (await create_fixed_asset_disposal(db,f.company,f.asset,data,f.actor)).id == row.id
        with pytest.raises(FixedAssetDisposalError,match='already assigned'):
            await create_fixed_asset_disposal(db,f.company,f.asset,data.model_copy(update={'request_key':'duplicate-sale'}),f.actor)
        with pytest.raises(AccountingReversalError,match='sale source'):
            await reverse_journal_entry(db,f.company,sale.id,day(28),f.actor)
        await reverse_fixed_asset_disposal(db,f.company,f.asset,row.id,day(28),'undo-sale',f.actor)
        assert asset.original_cost == 10000 and asset.salvage_value == 1000
        assert await _accumulated(db,f.company,f.asset) == Decimal('150')
        await reverse_journal_entry(db,f.company,sale.id,day(28),f.actor)

async def test_improvement_remaining_life_is_prospective_and_reversible(engine):
    from app.models.accounting_period import AccountingPeriod
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db)
        first=await depreciation(db,f)
        assert first.amount==Decimal('150')
        source=JournalEntry(company_id=f.company,entry_date=day(28),status='posted',created_by=f.actor,
            lines=[JournalEntryLine(line_no=1,account_id=f.depreciation_expense_account,debit=600,credit=0),
                   JournalEntryLine(line_no=2,account_id=f.counterpart_account,debit=0,credit=600)])
        db.add(source);await db.flush()
        change=await create_fixed_asset_repair_improvement(db,f.company,f.asset,FixedAssetRepairImprovementCreate(
            operation_type='improvement',operation_date=day(28),amount=600,new_remaining_life_months=30,
            source_journal_entry_line_id=source.lines[0].id,request_key='life'),f.actor)
        db.add(AccountingPeriod(company_id=f.company,year=2026,month=3,start_date=date(2026,3,1),end_date=date(2026,3,31),status='open',is_locked=False))
        await db.flush()
        after=await create_and_post_depreciation(db=db,company_id=f.company,fixed_asset_id=f.asset,
            request_key='new-life',period_start=date(2026,3,1),period_end=date(2026,3,31),posting_date=date(2026,3,31),created_by=f.actor)
        assert after.amount == Decimal('315') # (10600 - 1000 - 150) / 30
        assert first.amount == Decimal('150')
        with pytest.raises(HTTPException,match='later'):
            await reverse_fixed_asset_repair_improvement(db,f.company,f.asset,change.id,date(2026,3,31),'bad',f.actor)
        await reverse_depreciation(db=db,company_id=f.company,depreciation_id=after.id,reversal_date=date(2026,3,31),reversed_by=f.actor)
        await reverse_fixed_asset_repair_improvement(db,f.company,f.asset,change.id,date(2026,3,31),'undo-life',f.actor)
        asset=await db.get(FixedAsset,f.asset)
        assert asset.useful_life_months==60 and asset.original_cost==10000

async def test_late_posting_cannot_charge_months_out_of_order(engine):
    from app.models.accounting_period import AccountingPeriod
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db)
        db.add(AccountingPeriod(company_id=f.company,year=2026,month=3,start_date=date(2026,3,1),end_date=date(2026,3,31),status='open',is_locked=False))
        await db.flush()
        args=dict(db=db,company_id=f.company,fixed_asset_id=f.asset,posting_date=date(2026,3,31),created_by=f.actor)
        await create_and_post_depreciation(**args,request_key='march',period_start=date(2026,3,1),period_end=date(2026,3,31))
        with pytest.raises(FixedAssetDepreciationError,match='later depreciation periods'):
            await create_and_post_depreciation(**args,request_key='feb-late',period_start=day(1),period_end=day(28))

async def test_partial_disposal_preserves_annual_depreciation_basis(engine):
    from app.models.fixed_asset import FixedAssetDepreciationMethod
    from app.services.fixed_asset_depreciation_service import _schedule_inputs, _calculate_amount
    from app.services.fixed_asset_revaluation_impairment_service import _accumulated
    async with AsyncSession(engine,expire_on_commit=False) as db:
        f=await seed(db)
        asset=await db.get(FixedAsset,f.asset)
        asset.depreciation_method=FixedAssetDepreciationMethod.DOUBLE_DIMINISHING_BALANCE
        await db.flush()
        row=await depreciation(db,f)
        assert row.amount==Decimal('333.33')
        await create_fixed_asset_disposal(db,f.company,f.asset,FixedAssetDisposalCreate(
            disposal_date=day(28),disposal_account_id=f.disposal_account,disposal_fraction=Decimal('.25'),request_key='quarter'),f.actor)
        accumulated=await _accumulated(db,f.company,f.asset)
        schedule=await _schedule_inputs(db,asset,date(2026,3,1),accumulated)
        assert schedule['year_start_carrying']==Decimal('7500.00')
        assert _calculate_amount(asset=asset,accumulated_before=accumulated,period_start=date(2026,3,1),period_end=date(2026,3,31),**schedule)==Decimal('250.00')
