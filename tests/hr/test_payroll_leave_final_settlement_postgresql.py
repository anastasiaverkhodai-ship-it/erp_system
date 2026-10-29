"""Real source services: termination -> compensation -> payroll -> taxes -> bank."""
import os
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_source_locking import sources

pytestmark=[pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1', reason='Isolated PostgreSQL')]


@pytest.mark.parametrize('with_severance',[False,True])
async def test_termination_compensation_flows_once_through_payroll_and_payment(employee_engine, with_severance, history_daily=100, before_compensation=None, expected_compensation=1700):
    from app.models.employee import Employee
    from app.models.payroll_opening import PayrollEarningsHistory
    from app.models.account import Account
    from app.models.accounting_period import AccountingPeriod
    from app.models.bank_account import BankAccount
    from app.models.payroll import PayrollCalculationLine
    from app.schemas.employment_event import EmploymentEventCreate
    from app.schemas.payroll import PayrollPeriodCreate, PayrollInputCreate
    from app.schemas.time_attendance import AttendanceCreate
    from app.schemas.payroll_leave_balance import LeaveAccountCreate, LeaveAccrualCreate
    from app.schemas.payroll_leave_compensation import LeaveCompensationCreate
    from app.services.employment_event_service import create_employment_event
    from app.services.payroll_period_service import create_payroll_period, finalize_payroll_period
    from app.services.time_attendance_service import create_attendance
    from app.services.payroll_input_service import create_payroll_input
    from app.services.payroll_calculation_service import calculate_payroll_input
    from app.services.payroll_leave_balance_service import create_leave_account, accrue_leave_days
    from app.services.payroll_leave_compensation_service import create_leave_compensation
    from app.services.payroll_statutory_service import create_statutory_rate, calculate_payroll_statutory_result
    from app.models.payroll_statutory import PayrollStatutoryComponent as Component
    from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
    from app.services.payroll_disbursement_service import create_payroll_disbursement, generate_and_post_payroll_disbursement_journal
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f,c,_=await sources(db)
        company,actor=f['company'],f['actor']
        employee=await db.get(Employee,c.employee_id)
        employee.payment_iban='UA123456789012345678901234567'
        event=await create_employment_event(db,company_id=company,contract_id=c.id,created_by=actor,
            data=EmploymentEventCreate(event_type='termination',effective_date=date(2026,9,30),
                order_number='TERM-FINAL',order_date=date(2026,9,30),reason='Documented termination',request_key='termination',
                termination_basis=dict(ground='redundancy',labour_code_provision='40.1',
                    source_reference='Verified redundancy order') if with_severance else None))
        for month in range(2,9):
            days=monthrange(2026,month)[1]
            db.add(PayrollEarningsHistory(company_id=company,employment_contract_id=c.id,
                month=date(2026,month,1),cutover_date=date(2026,9,1),gross_amount=D(days)*history_daily,
                vacation_earnings=D(days)*history_daily,sick_earnings=D(days)*history_daily,vacation_days=days,sick_days=days,
                source_reference='Verified preceding payroll',request_fingerprint=str(month),created_by=actor))
        period=await create_payroll_period(db,company_id=company,created_by=actor,
            data=PayrollPeriodCreate(year=2026,month=9,start_date=date(2026,9,1),end_date=date(2026,9,30)))
        for day in range(1,31):
            if date(2026,9,day).weekday()>4:
                continue
            await create_attendance(db,company_id=company,created_by=actor,data=AttendanceCreate(
                employment_contract_id=c.id,work_date=date(2026,9,day),status='present',
                actual_start_at=datetime(2026,9,day,9,tzinfo=ZoneInfo('Europe/Kyiv')),
                actual_end_at=datetime(2026,9,day,17,tzinfo=ZoneInfo('Europe/Kyiv'))))
        inputs=await create_payroll_input(db,company_id=company,payroll_period_id=period.id,
            created_by=actor,data=PayrollInputCreate(employment_contract_id=c.id))
        account=await create_leave_account(db,company_id=company,actor_id=actor,data=LeaveAccountCreate(
            employment_contract_id=c.id,leave_kind='annual',entitlement_code='basic',
            period_start=date(2026,1,10),period_end=date(2027,1,9),
            segments=[dict(start=date(2026,1,10),end=date(2027,1,9),annual_days=24)],
            source_reference='First employment year',request_key='rights'))
        if before_compensation is not None:
            await accrue_leave_days(db,company_id=company,account_id=account.id,actor_id=actor,
                data=LeaveAccrualCreate(through=date(2026,7,31),source_reference='Verified preceding service',request_key='preceding-accrue'))
            await before_compensation(db,company,actor,c,account)
        await accrue_leave_days(db,company_id=company,account_id=account.id,actor_id=actor,
            data=LeaveAccrualCreate(through=date(2026,9,30),source_reference='Verified employment service',request_key='accrue'))
        compensation=await create_leave_compensation(db,company_id=company,actor_id=actor,
            data=LeaveCompensationCreate(termination_event_id=event.id,payroll_input_id=inputs.id,
                request_key='compensate',entitlement_review_reference='All leave rights reviewed'))
        assert compensation.gross_amount==expected_compensation
        if with_severance:
            from app.models.payroll_termination_severance import PayrollTerminationSeverance
            db.add(PayrollTerminationSeverance(company_id=company,payroll_input_id=inputs.id,
                employment_contract_id=c.id,termination_event_id=event.id,revision=1,
                request_key='documented-severance',termination_date=event.effective_date,order_number=event.order_number,
                policy_ground='redundancy',policy_category='average_months',labour_code_provision='40.1',
                source_reference='Verified redundancy order',multiplier=1,basis_as_of=event.effective_date,
                basis_reference='Confirmed two-month average',base_amount=12000,gross_amount=12000,
                currency_code='UAH',created_by=actor))
            await db.flush()
        await finalize_payroll_period(db,company_id=company,payroll_period_id=period.id,finalized_by=actor)
        calc=await calculate_payroll_input(db,company_id=company,payroll_input_id=inputs.id,calculated_by=actor)
        assert calc.gross_amount==22000+expected_compensation+(12000 if with_severance else 0)
        assert (await calculate_payroll_input(db,company_id=company,payroll_input_id=inputs.id,calculated_by=actor)).id==calc.id
        lines=list((await db.scalars(select(PayrollCalculationLine).where(
            PayrollCalculationLine.payroll_calculation_id==calc.id,PayrollCalculationLine.line_type=='leave_compensation'))).all())
        assert len(lines)==1 and lines[0].amount==expected_compensation and lines[0].source_leave_compensation_line_id
        for component,rate in [(Component.PERSONAL_INCOME_TAX,D('.18')),(Component.MILITARY_LEVY,D('.05')),
                               (Component.UNIFIED_SOCIAL_CONTRIBUTION,D('.22'))]:
            await create_statutory_rate(db,company_id=company,component=component,rate=rate,
                effective_from=date(2026,1,1),effective_to=None,actor_user_id=actor)
        if with_severance:
            from app.services.payroll_severance_trusted_classification import persist_trusted_severance_snapshot
            await persist_trusted_severance_snapshot(db,company_id=company,payroll_calculation_id=calc.id)
        tax=await calculate_payroll_statutory_result(db,company_id=company,payroll_calculation_id=calc.id,actor_user_id=actor)
        assert tax.net_amount==((D(22000)+expected_compensation)*D('.77'))+(D('9240') if with_severance else 0) and tax.employer_contribution_amount==((D(22000)+expected_compensation)*D('.22'))
        db.add(AccountingPeriod(company_id=company,year=2026,month=9,start_date=date(2026,9,1),
            end_date=date(2026,9,30),status='open',is_locked=False))
        accounts={}
        for code in ('92','66','64','65','311'):
            kind='asset' if code=='311' else 'expense' if code=='92' else 'liability'
            row=Account(company_id=company,code=code,name='Payroll '+code,account_type=kind,
                normal_balance='credit' if kind=='liability' else 'debit',is_postable=True,is_active=True,is_system=True)
            db.add(row);await db.flush();accounts[code]=row.id
        bank=BankAccount(company_id=company,name='Payroll bank',account_number='FINAL-SETTLEMENT',
            currency_code='UAH',accounting_account_id=accounts['311'],is_active=True)
        db.add(bank);await db.flush()
        journal=await generate_and_post_payroll_journal_entry(db,company_id=company,payroll_calculation_id=calc.id,created_by=actor)
        assert sum(line.debit for line in journal.lines)==((D(22000)+expected_compensation)*D('1.22'))+(12000 if with_severance else 0)
        payout=await create_payroll_disbursement(db,company_id=company,payroll_calculation_id=calc.id,
            bank_account_id=bank.id,payment_date=date(2026,9,30),created_by=actor)
        assert payout.amount==((D(22000)+expected_compensation)*D('.77'))+(9240 if with_severance else 0)
        paid=await generate_and_post_payroll_disbursement_journal(db,company_id=company,
            payroll_disbursement_id=payout.id,created_by=actor)
        assert sum(line.credit for line in paid.lines if line.account_id==accounts['311'])==((D(22000)+expected_compensation)*D('.77'))+(9240 if with_severance else 0)
        assert (await generate_and_post_payroll_disbursement_journal(db,company_id=company,
            payroll_disbursement_id=payout.id,created_by=actor)).id==paid.id
        await db.commit()
