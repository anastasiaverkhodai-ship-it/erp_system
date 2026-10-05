import os
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_employment_structure import setup,contract
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.journal_entry import JournalEntry
from app.models.payroll import PayrollPeriod
from app.models.payroll_opening import PayrollOpeningDebt,PayrollOpeningPackage
from app.schemas.opening_balance import OpeningBalanceCreate
from app.schemas.payroll_opening import PayrollOpeningAttach,PayrollEarningsHistoryInput,PayrollLeaveOpeningInput
from app.schemas.payroll import PayrollInputCreate
from app.services.opening_balance_service import create_opening_balance,post_opening_balance,reverse_opening_balance,OpeningBalanceError
from app.services.accounting_reversal import reverse_journal_entry,AccountingReversalError
from app.services.payroll_opening_service import attach_payroll_opening,import_earnings_history,import_leave_opening,PayrollOpeningError
from app.services.payroll_input_service import create_payroll_input,PayrollInputDerivationError
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def test_opening_debt_matches_gl_without_duplicate_posting(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db)
        payroll=Account(company_id=f['company'],code='66',name='Payroll payable',account_type='liability',normal_balance='credit',is_system=True,is_postable=True,is_active=True)
        equity=Account(company_id=f['company'],code='44',name='Opening equity',account_type='equity',normal_balance='credit',is_system=True,is_postable=True,is_active=True)
        db.add_all([payroll,equity,AccountingPeriod(company_id=f['company'],year=2026,month=1,
            start_date=date(2026,1,1),end_date=date(2026,1,31),status='open',is_locked=False)])
        await db.flush()
        opening=await create_opening_balance(db,f['company'],f['actor'],OpeningBalanceCreate(
            request_key='opening',opening_date=date(2026,1,10),lines=[{'account_id':equity.id,'debit':'1000'},
            {'account_id':payroll.id,'credit':'1000'}]))
        await post_opening_balance(db,f['company'],opening.id)
        count=await db.scalar(select(func.count(JournalEntry.id)))
        data=PayrollOpeningAttach(request_key='detail',source_reference='Verified closing payroll register',
            debts=[{'employee_id':f['employee'],'net_amount':'999'}])
        with pytest.raises(PayrollOpeningError,match='reconcile'):
            await attach_payroll_opening(db,company_id=f['company'],opening_balance_id=opening.id,data=data,created_by=f['actor'])
        assert await db.scalar(select(func.count(PayrollOpeningPackage.id)))==0
        data=PayrollOpeningAttach(request_key='detail',source_reference='Verified closing payroll register',
            debts=[{'employee_id':f['employee'],'net_amount':'1000'}])
        package=await attach_payroll_opening(db,company_id=f['company'],opening_balance_id=opening.id,data=data,created_by=f['actor'])
        assert (await attach_payroll_opening(db,company_id=f['company'],opening_balance_id=opening.id,data=data,created_by=f['actor'])).id==package.id
        assert await db.scalar(select(func.count(JournalEntry.id)))==count
        assert await db.scalar(select(PayrollOpeningDebt.net_amount))==D('1000')
        from app.services.payroll_employee_balance_service import employee_payroll_balance
        before=await employee_payroll_balance(db,company_id=f['company'],employee_id=f['employee'],as_of=date(2026,1,9))
        assert before['balances']==[]
        after=await employee_payroll_balance(db,company_id=f['company'],employee_id=f['employee'],as_of=date(2026,1,10))
        assert after['complete']
        assert after['balances'][0]['balance']==D('1000')

        with pytest.raises(OpeningBalanceError,match='explicit correction'):
            await reverse_opening_balance(db,f['company'],opening.id,date(2026,1,11),f['actor'])
        with pytest.raises(AccountingReversalError,match='explicit correction'):
            await reverse_journal_entry(db,f['company'],opening.journal_entry_id,date(2026,1,11),f['actor'])


async def test_history_and_leave_are_idempotent_and_not_recalculated(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db);c=await contract(db,f)
        history=PayrollEarningsHistoryInput(month=date(2026,1,1),cutover_date=date(2026,2,1),
            gross_amount='10000',vacation_earnings='10000',sick_earnings='10000',vacation_days=22,sick_days=22,source_reference='January source register')
        first=await import_earnings_history(db,company_id=f['company'],contract_id=c.id,data=history,created_by=f['actor'])
        assert (await import_earnings_history(db,company_id=f['company'],contract_id=c.id,data=history,created_by=f['actor'])).id==first.id
        with pytest.raises(PayrollOpeningError,match='different data'):
            await import_earnings_history(db,company_id=f['company'],contract_id=c.id,
                data=history.model_copy(update={'gross_amount':D('10001')}),created_by=f['actor'])
        leave=PayrollLeaveOpeningInput(working_year_start=date(2026,1,10),working_year_end=date(2027,1,9),
            as_of=date(2026,2,1),remaining_days='1.50',source_reference='Signed leave balance')
        balance=await import_leave_opening(db,company_id=f['company'],contract_id=c.id,data=leave,created_by=f['actor'])
        assert balance.remaining_days==D('1.50')
        assert (await import_leave_opening(db,company_id=f['company'],contract_id=c.id,data=leave,created_by=f['actor'])).id==balance.id
        period=PayrollPeriod(company_id=f['company'],year=2026,month=1,start_date=date(2026,1,1),end_date=date(2026,1,31),status='draft',created_by=f['actor'])
        db.add(period);await db.flush()
        with pytest.raises(PayrollInputDerivationError,match='imported earnings'):
            await create_payroll_input(db,company_id=f['company'],payroll_period_id=period.id,created_by=f['actor'],
                data=PayrollInputCreate(employment_contract_id=c.id))


async def test_opening_input_migration_preserves_history(employee_engine):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import text
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    path=Path(__file__).parents[2]/'alembic/versions/13d4a7b8c007_payroll_opening_inputs.py'
    spec=importlib.util.spec_from_file_location('payroll_opening_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    def run(sync,method):
        with Operations.context(MigrationContext.configure(sync)):
            getattr(migration,method)()
    async with employee_engine.begin() as connection:
        for table in ('payroll_opening_debts','payroll_opening_packages','payroll_leave_openings','payroll_earnings_history'):
            await connection.execute(text(f'DROP TABLE {table}'))
        await connection.run_sync(run,'upgrade')
        await connection.run_sync(run,'downgrade')
        await connection.run_sync(run,'upgrade')
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db);c=await contract(db,f)
        row=await import_leave_opening(db,company_id=f['company'],contract_id=c.id,created_by=f['actor'],
            data=PayrollLeaveOpeningInput(working_year_start=date(2026,1,10),working_year_end=date(2027,1,9),
                as_of=date(2026,2,1),remaining_days='2',source_reference='Opening leave register'))
        with pytest.raises(Exception,match='immutable'):
            async with db.begin_nested():
                await db.execute(text('UPDATE payroll_leave_openings SET remaining_days=0 WHERE id=:id'),{'id':row.id})
        with pytest.raises(Exception,match='history must be preserved'):
            async with db.begin_nested():
                connection=await db.connection()
                await connection.run_sync(run,'downgrade')
