"""Automatic top-up, snapshot/retry, tax base and historical average integration."""
import os
from datetime import date, datetime, time
from decimal import Decimal as D
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_source_locking import sources
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.time_attendance import WorkScheduleDay
from app.models.payroll import PayrollCalculationLine
from app.schemas.payroll import PayrollPeriodCreate, PayrollInputCreate
from app.schemas.payroll_supplement import PayrollSupplementCreate
from app.schemas.time_attendance import AttendanceCreate
from app.services.time_attendance_service import create_attendance
from app.services.payroll_period_service import create_payroll_period, finalize_payroll_period
from app.services.payroll_input_service import create_payroll_input
from app.services.payroll_calculation_service import calculate_payroll_input, PayrollCalculationDerivationError, PayrollCalculationConflictError
from app.services.payroll_supplement_service import create_payroll_supplement

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def inputs(db,part_time=False, midmonth_part_time=False, adjustment=D(0)):
    f,contract,schedule=await sources(db)
    if midmonth_part_time:
        from app.schemas.employment_event import EmploymentEventCreate
        from app.services.employment_event_service import create_employment_event
        await create_employment_event(db, company_id=f['company'], contract_id=contract.id,
            created_by=f['actor'], data=EmploymentEventCreate(event_type='transfer',
                effective_date=date(2026,9,15), order_date=date(2026,9,15), order_number='PT-15',
                reason='Change in working arrangement', request_key='PT-15', work_arrangement='part_time'))
    rate=await db.scalar(select(EmployeeSalaryRate).where(EmployeeSalaryRate.employment_contract_id==contract.id))
    rate.amount=D('3000') if part_time else D('6000')
    if part_time:
        contract.work_arrangement='part_time'
        for day in (await db.scalars(select(WorkScheduleDay).where(WorkScheduleDay.work_schedule_id==schedule.id))).all():
            day.end_time=time(13)
    await db.flush()
    period=await create_payroll_period(db,company_id=f['company'],created_by=f['actor'],
        data=PayrollPeriodCreate(year=2026,month=9,start_date=date(2026,9,1),end_date=date(2026,9,30)))
    for day in range(1,31):
        if date(2026,9,day).weekday()>4: continue
        await create_attendance(db,company_id=f['company'],created_by=f['actor'],data=AttendanceCreate(
            employment_contract_id=contract.id,work_date=date(2026,9,day),status='present',
            actual_start_at=datetime(2026,9,day,9,tzinfo=ZoneInfo('Europe/Kyiv')),
            actual_end_at=datetime(2026,9,day,13 if part_time else (19 if day==1 else 17),tzinfo=ZoneInfo('Europe/Kyiv'))))
    source=await create_payroll_input(db,company_id=f['company'],payroll_period_id=period.id,
        created_by=f['actor'],data=PayrollInputCreate(employment_contract_id=contract.id, manual_adjustment_amount=adjustment,
            manual_adjustment_reason='Documented current-period correction' if adjustment else None))
    if not part_time:
        for kind,amount,minutes in [('bonus',D('1000'),0),('holiday_bonus',D('500'),0),('overtime',D('0'),120)]:
            await create_payroll_supplement(db,company_id=f['company'],payroll_input_id=source.id,created_by=f['actor'],
                data=PayrollSupplementCreate(kind=kind,amount=amount,minutes=minutes,
                    work_date=date(2026,9,1),source_reference='Test order',request_key=kind))
    await finalize_payroll_period(db,company_id=f['company'],payroll_period_id=period.id,finalized_by=f['actor'])
    return f,source


async def test_monthly_top_up_excludes_overtime_and_holiday_bonus(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,source=await inputs(db)
        result=await calculate_payroll_input(db,company_id=f['company'],payroll_input_id=source.id,calculated_by=f['actor'])
        assert result.gross_amount==D('9283.36')
        assert D(result.minimum_wage_assessment['top_up'])==1647
        assert D(result.minimum_wage_assessment['included_earnings'])==7000
        lines=list((await db.scalars(select(PayrollCalculationLine).where(PayrollCalculationLine.payroll_calculation_id==result.id))).all())
        assert sum(row.amount for row in lines)==result.gross_amount
        assert [row.amount for row in lines if row.line_type=='minimum_wage']==[D('1647')]
        assert (await calculate_payroll_input(db,company_id=f['company'],payroll_input_id=source.id,calculated_by=f['actor'])).id==result.id


async def test_part_time_norm_is_explicit_and_saved_request_cannot_change(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,source=await inputs(db,part_time=True)
        args=dict(company_id=f['company'],payroll_input_id=source.id,calculated_by=f['actor'])
        with pytest.raises(PayrollCalculationDerivationError,match='documented full-time norm'):
            await calculate_payroll_input(db,**args)
        context=dict(full_time_norm_minutes=10560,norm_reference='Full-time calendar order')
        result=await calculate_payroll_input(db,**args,minimum_wage_context=context)
        assert result.gross_amount==D('4323.50')
        assert D(result.minimum_wage_assessment['top_up'])==D('1323.50')
        assert (await calculate_payroll_input(db,**args,minimum_wage_context=context)).id==result.id
        with pytest.raises(PayrollCalculationConflictError,match='different minimum-wage context'):
            await calculate_payroll_input(db,**args,minimum_wage_context=dict(context,full_time_norm_minutes=10000))


async def test_top_up_flows_through_taxes_and_balanced_journal(employee_engine):
    from app.models.account import Account
    from app.models.accounting_period import AccountingPeriod
    from app.models.journal_entry_line import JournalEntryLine
    from app.models.payroll_statutory import PayrollStatutoryComponent as Component
    from app.services.account_types import AccountType, AccountNormalBalance
    from app.services.payroll_statutory_service import create_statutory_rate, calculate_payroll_statutory_result
    from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, source = await inputs(db)
        calc = await calculate_payroll_input(db, company_id=f['company'], payroll_input_id=source.id, calculated_by=f['actor'])
        for component, rate in [(Component.PERSONAL_INCOME_TAX, D('.18')),
                                (Component.MILITARY_LEVY, D('.05')),
                                (Component.UNIFIED_SOCIAL_CONTRIBUTION, D('.22'))]:
            await create_statutory_rate(db, company_id=f['company'], component=component, rate=rate,
                effective_from=date(2026, 1, 1), effective_to=None, actor_user_id=f['actor'])
        tax = await calculate_payroll_statutory_result(db, company_id=f['company'],
            payroll_calculation_id=calc.id, actor_user_id=f['actor'])
        assert tax.gross_amount == D('9283.36')
        assert tax.employee_withholding_amount == D('2135.17')
        assert tax.net_amount == D('7148.19')
        assert tax.employer_contribution_amount == D('2042.34')
        db.add(AccountingPeriod(company_id=f['company'], year=2026, month=9,
            start_date=date(2026,9,1), end_date=date(2026,9,30), status='open', is_locked=False))
        for code in ('92','66','64','65'):
            db.add(Account(company_id=f['company'], code=code, name='Payroll '+code,
                account_type=AccountType.EXPENSE if code=='92' else AccountType.LIABILITY,
                normal_balance=AccountNormalBalance.DEBIT if code=='92' else AccountNormalBalance.CREDIT,
                is_postable=True, is_system=True, is_active=True))
        await db.flush()
        journal = await generate_and_post_payroll_journal_entry(db, company_id=f['company'],
            payroll_calculation_id=calc.id, created_by=f['actor'])
        rows = list((await db.scalars(select(JournalEntryLine).where(JournalEntryLine.journal_entry_id==journal.id))).all())
        assert sum(row.debit for row in rows) == sum(row.credit for row in rows) == D('11325.70')
        assert any(row.debit == calc.gross_amount for row in rows)
        assert (await generate_and_post_payroll_journal_entry(db, company_id=f['company'],
            payroll_calculation_id=calc.id, created_by=f['actor'])).id == journal.id


async def test_midmonth_work_arrangement_change_requires_documented_norm(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, source = await inputs(db, midmonth_part_time=True)
        with pytest.raises(PayrollCalculationDerivationError, match='documented full-time norm'):
            await calculate_payroll_input(db, company_id=f['company'], payroll_input_id=source.id,
                calculated_by=f['actor'])


@pytest.mark.parametrize('amount,category,expected_gross,expected_top_up',[
    (D('-500'),'ordinary_salary',D('9283.36'),D('2147')),
    (D('500'),'holiday_bonus',D('9783.36'),D('1647')),
])
async def test_documented_adjustment_classification_changes_only_eligible_basis(
    employee_engine,amount,category,expected_gross,expected_top_up):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,source=await inputs(db,adjustment=amount)
        args=dict(company_id=f['company'],payroll_input_id=source.id,calculated_by=f['actor'])
        with pytest.raises(PayrollCalculationDerivationError,match='explicit minimum-wage classification'):
            await calculate_payroll_input(db,**args)
        context=dict(manual_adjustment_category=category,manual_adjustment_reference='Correction order 1')
        result=await calculate_payroll_input(db,**args,minimum_wage_context=context)
        assert result.gross_amount==expected_gross
        assert D(result.minimum_wage_assessment['top_up'])==expected_top_up
        assert result.minimum_wage_assessment['request']==context
        with pytest.raises(PayrollCalculationConflictError,match='different minimum-wage context'):
            await calculate_payroll_input(db,**args,minimum_wage_context=dict(context,manual_adjustment_reference='Another order'))


async def test_adjustment_cannot_reduce_another_earning_category(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,source=await inputs(db,adjustment=D('-1500'))
        with pytest.raises(PayrollCalculationDerivationError,match='exceeds the current-period earnings'):
            await calculate_payroll_input(db,company_id=f['company'],payroll_input_id=source.id,calculated_by=f['actor'],
                minimum_wage_context=dict(manual_adjustment_category='production_bonus',manual_adjustment_reference='Order'))
