from datetime import date, datetime, timezone
from decimal import Decimal as D
from uuid import uuid4
import pytest
from sqlalchemy import select,text
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from test_payroll_sick_correction_preview_postgresql import install_revision_guards
from test_payroll_correction_basis_postgresql import protect
from app.models.leave_request import LeaveRequest
from app.models.payroll_vacation import PayrollVacationCalculation
from app.models.payroll import PayrollPeriod,PayrollInput,PayrollCalculation,PayrollCalculationLine
from app.models.user import User
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.bank_account import BankAccount
from app.services.payroll_correction_basis import make_basis,replay_basis
from app.services.payroll_statutory_service import create_statutory_rate,calculate_payroll_statutory_result
from app.models.payroll_statutory import PayrollStatutoryComponent as Component
from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
from app.services.payroll_disbursement_service import create_payroll_disbursement,generate_and_post_payroll_disbursement_journal
from app.schemas.payroll_leave_correction import LeaveCorrectionRequestCreate,LeaveCorrectionRange
from app.services.payroll_leave_correction_service import request_leave_correction,apply_leave_correction,LeaveCorrectionError
from app.services.payroll_sick_settlement_service import sick_settlement_summary

pytestmark=pytest.mark.asyncio


async def install(engine):
    await install_revision_guards(engine);await protect(engine)
    # Keep deployed compensation immutability/reconciliation enabled, rather
    # than relying only on metadata-created tables.
    import ast
    from pathlib import Path
    async with engine.begin() as conn:
        for file in ('13d4a7b8c034_leave_compensation_provenance.py','13d4a7b8c035_leave_compensation_reversals.py'):
            tree=ast.parse(Path('alembic/versions',file).read_text())
            upgrade=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='upgrade')
            for node in ast.walk(upgrade):
                if isinstance(node,ast.Call) and node.args and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,str):
                    sql=node.args[0].value.strip()
                    if sql.startswith(('CREATE FUNCTION','CREATE OR REPLACE FUNCTION','CREATE TRIGGER','CREATE CONSTRAINT TRIGGER')):
                        await conn.execute(text(sql))
        for table in ('payroll_leave_compensations','payroll_leave_compensation_lines'):
            await conn.execute(text(f'CREATE CONSTRAINT TRIGGER trg_test_reconcile_{table} AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION payroll_validate_leave_compensation()'))
    from importlib.util import spec_from_file_location,module_from_spec
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic import op
    from unittest.mock import patch
    from contextlib import ExitStack
    spec=spec_from_file_location('leave064','alembic/versions/13d4a7b8c064_leave_financial_corrections.py')
    module=module_from_spec(spec);spec.loader.exec_module(module)
    async with engine.begin() as conn:
        def run(sync):
            with Operations.context(MigrationContext.configure(sync)),ExitStack() as stack:
                for name in ('add_column','alter_column','create_check_constraint','create_table','create_foreign_key','drop_constraint'):
                    stack.enter_context(patch.object(op,name))
                module.upgrade()
        await conn.run_sync(run)


async def setup(db, *, with_salary=False, post=True):
    company,user,employee,contract=await _seed_identity(db)
    reviewer=User(email=uuid4().hex+'@example.invalid',password_hash='unused',first_name='Independent',last_name='Reviewer',is_active=True)
    db.add(reviewer);await db.flush()
    leave=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type='annual',
        start_date=date(2026,9,1),end_date=date(2026,9,10),status='approved',requested_by=user.id,approved_by=user.id,approved_at=datetime.now(timezone.utc))
    db.add(leave);await db.flush()
    from app.services.payroll_leave_balance_service import create_leave_account,accrue_leave_days,allocate_leave_days
    from app.schemas.payroll_leave_balance import LeaveAccountCreate,LeaveAccrualCreate
    account=await create_leave_account(db,company_id=company.id,actor_id=user.id,data=LeaveAccountCreate(
        employment_contract_id=contract.id,leave_kind='annual',entitlement_code='basic',
        period_start=date(2026,1,1),period_end=date(2026,12,31),
        segments=[dict(start=date(2026,1,1),end=date(2026,12,31),annual_days=24)],
        source_reference='Documented annual rights',request_key='account'))
    await accrue_leave_days(db,company_id=company.id,account_id=account.id,actor_id=user.id,
        data=LeaveAccrualCreate(through=date(2026,8,31),source_reference='Verified service calendar',request_key='accrue'))
    await allocate_leave_days(db,company_id=company.id,leave_request_id=leave.id,
        allocations=[dict(account_id=account.id,bucket='from_2024',days=D(10))],
        request_key='allocate',source_reference='Approved annual leave order',actor_id=user.id)
    source=PayrollVacationCalculation(company_id=company.id,employment_contract_id=contract.id,
        leave_request_id=leave.id,leave_days=10,reference_period_start=date(2026,1,1),reference_period_end=date(2026,8,31),
        eligible_earnings=10000,eligible_days=100,average_daily_amount=100,vacation_pay_amount=1000,
        currency_code='UAH',rule_code='UA_ORDER_100',rule_version='verified',calculated_by=user.id)
    period=PayrollPeriod(company_id=company.id,year=2026,month=9,start_date=date(2026,9,1),end_date=date(2026,9,30),created_by=user.id)
    db.add_all([source,period]);await db.flush()
    pi=PayrollInput(company_id=company.id,payroll_period_id=period.id,employment_contract_id=contract.id,created_by=user.id)
    db.add(pi);await db.flush()
    lines=[dict(line_no=1,line_type='vacation_pay',quantity=D(10),rate=D(100),amount=D(1000),
        currency_code='UAH',source_vacation_calculation_id=source.id)]
    facts=dict(rate_type='monthly',minimum_rate='8647',ordinary_worked_minutes=0,default_norm_minutes=10560,
        earnings=[dict(category='vacation_pay',amount='1000')],adjustment='0',has_adjustment=False,
        period_start='2026-09-01',period_end='2026-09-30',rule_version='UA-4695-IX-2026-v1')
    if with_salary:
        from app.models.employee_salary_rate import EmployeeSalaryRate
        from app.models.payroll import PayrollInputSalarySlice
        from app.models.time_attendance import AttendanceRecord
        rate=EmployeeSalaryRate(company_id=company.id,employment_contract_id=contract.id,
            rate_type='monthly',amount=22000,currency_code='UAH',effective_from=date(2026,9,1),created_by=user.id)
        db.add(rate);await db.flush()
        pi.monthly_norm_minutes=10560;pi.worked_minutes=5280;pi.scheduled_minutes=10560
        db.add(PayrollInputSalarySlice(company_id=company.id,payroll_input_id=pi.id,salary_rate_id=rate.id,
            salary_rate_type='monthly',salary_rate_amount=22000,currency_code='UAH',
            effective_from=period.start_date,effective_to=period.end_date,scheduled_minutes=10560,worked_minutes=5280))
        db.add(AttendanceRecord(company_id=company.id,employment_contract_id=contract.id,
            leave_request_id=leave.id,work_date=date(2026,9,10),status='leave',worked_minutes=0,
            source='system',created_by=user.id))
        await db.flush()
        lines.append(dict(line_no=2,line_type='salary',quantity=D(30),rate=D(22000),amount=D(11000),
            currency_code='UAH',salary_rate_type='monthly',source_salary_rate_id=rate.id,
            source_effective_from=period.start_date,source_effective_to=period.end_date))
        facts['ordinary_worked_minutes']=5280
        facts['earnings'].append(dict(category='ordinary_salary',amount='11000'))
    basis=make_basis(company_id=company.id,period=period,payroll_input=pi,salary_slices=[],facts=facts,lines=lines)
    gross,lines,assessment=replay_basis(basis,{})
    calc=PayrollCalculation(company_id=company.id,payroll_period_id=period.id,payroll_input_id=pi.id,
        employment_contract_id=contract.id,currency_code='UAH',gross_amount=gross,calculated_by=user.id,
        minimum_wage_assessment=dict(assessment,correction_basis=basis))
    db.add(calc);await db.flush()
    db.add_all([PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calc.id,**line) for line in lines]);await db.flush();await db.refresh(calc)
    for component,rate in [(Component.PERSONAL_INCOME_TAX,D('.18')),(Component.MILITARY_LEVY,D('.05')),(Component.UNIFIED_SOCIAL_CONTRIBUTION,D('.22'))]:
        await create_statutory_rate(db,company_id=company.id,component=component,rate=rate,effective_from=date(2026,1,1),effective_to=None,actor_user_id=user.id)
    await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
    accounts={}
    for code in ('92','94','378','66','64','65','311'):
        kind='expense' if code in ('92','94') else 'asset' if code in ('378','311') else 'liability'
        row=Account(company_id=company.id,code=code,name=code,account_type=kind,
            normal_balance='credit' if kind=='liability' else 'debit',is_postable=True,is_system=True,is_active=True)
        db.add(row);await db.flush();accounts[code]=row.id
    for month in (9,10):db.add(AccountingPeriod(company_id=company.id,year=2026,month=month,
        start_date=date(2026,month,1),end_date=date(2026,month,30 if month==9 else 31),status='open',is_locked=False))
    bank=BankAccount(company_id=company.id,name='Test bank',account_number=uuid4().hex,currency_code='UAH',accounting_account_id=accounts['311'],is_active=True)
    employee.payment_iban='UA213223130000026007233566001';db.add(bank);await db.flush()
    journal=await generate_and_post_payroll_journal_entry(db,company_id=company.id,payroll_calculation_id=calc.id,created_by=user.id) if post else None
    return company,user,reviewer,source,calc,bank,journal


@pytest.mark.parametrize('transfer',[False,True])
@pytest.mark.parametrize('retained_days',[0,5])
async def test_paid_vacation_correction_retains_payment_and_requires_review(employee_engine,transfer,retained_days):
    await install(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,reviewer,source,calc,bank,journal=await setup(db)
        payout=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=calc.id,
            bank_account_id=bank.id,payment_date=date(2026,9,30),created_by=user.id,request_key='paid')
        paid=await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=payout.id,created_by=user.id)
        data=LeaveCorrectionRequestCreate(target_kind='vacation',target_id=source.id,correction_date=date(2026,10,28),
            request_key='recall',reason='Documented recall after payroll recognition',order_reference='Order 123',
            consent_reference='Signed employee consent',annual_continuity_reference='Approved working-year schedule: main uninterrupted 14-day part',operation='recall',effective_date=date(2026,9,retained_days+1),
            recall_ground='industrial_accident',retained_ranges=[dict(start=date(2026,9,1),end=date(2026,9,retained_days))] if retained_days else [],
            transferred_ranges=[dict(start=date(2026,11,2),end=date(2026,11,11-retained_days))] if transfer else [])
        request=await request_leave_correction(db,company_id=company.id,actor_id=user.id,data=data)
        with pytest.raises(LeaveCorrectionError,match='independent'):
            await apply_leave_correction(db,company_id=company.id,request_id=request.id,actor_id=user.id)
        app=await apply_leave_correction(db,company_id=company.id,request_id=request.id,actor_id=reviewer.id)
        assert (await apply_leave_correction(db,company_id=company.id,request_id=request.id,actor_id=reviewer.id)).id==app.id
        new=await db.get(PayrollCalculation,app.payroll_calculation_ids[0])
        assert calc.gross_amount==1000 and new.gross_amount==retained_days*100
        from app.models.payroll_leave_correction import PayrollLeaveDayCorrection,PayrollLeaveTransfer
        returned=list((await db.scalars(select(PayrollLeaveDayCorrection).where(PayrollLeaveDayCorrection.company_id==company.id))).all())
        assert sum(row.days for row in returned)==10-retained_days
        links=list((await db.scalars(select(PayrollLeaveTransfer).where(PayrollLeaveTransfer.company_id==company.id))).all())
        assert len(links)==int(transfer)
        if transfer:
            transferred=await db.get(LeaveRequest,links[0].leave_request_id)
            assert transferred.status=='approved' and transferred.start_date==date(2026,11,2)
        summary=await sick_settlement_summary(db,company_id=company.id,calculation_id=new.id,as_of=data.correction_date)
        overpayment=D(770)-retained_days*77
        assert summary['employee_due']==0 and summary['employee_overpayment']==overpayment
        assert paid.status=='posted' and journal.status=='posted' and payout.reversed_on is None
        from app.services.payroll_sick_return_service import register_sick_return,SickReturnError
        from app.models.journal_entry import JournalEntry
        from app.models.journal_entry_line import JournalEntryLine
        from app.services.accounting_posting import post_journal_entry
        from app.models.payroll_sick_correction_application import PayrollSickPayrollRevision
        edge=await db.scalar(select(PayrollSickPayrollRevision).where(
            PayrollSickPayrollRevision.company_id==company.id,
            PayrollSickPayrollRevision.replacement_calculation_id==new.id))
        from app.models.journal_entry_line import JournalEntryLine
        posted_lines=(await db.execute(select(Account.code,JournalEntryLine.debit,JournalEntryLine.credit)
            .join(JournalEntryLine,JournalEntryLine.account_id==Account.id)
            .where(JournalEntryLine.journal_entry_id==edge.journal_entry_id))).all()
        assert not any(code=='94' for code,_,_ in posted_lines)
        assert sum(debit-credit for code,debit,credit in posted_lines if code=='92')==D('-122')*(10-retained_days)
        assert D(edge.replacement_financial['personal_income_tax'])-D(edge.original_financial['personal_income_tax'])==D('-18')*(10-retained_days)
        assert D(edge.replacement_financial['military_levy'])-D(edge.original_financial['military_levy'])==D('-5')*(10-retained_days)
        payable=await db.scalar(select(Account.id).where(Account.company_id==company.id,Account.code=='66'))
        # Real partial and final bank returns; neither rewrites the original pay.
        for index,amount in enumerate((D('100'),overpayment-100)):
            returned=JournalEntry(company_id=company.id,entry_date=data.correction_date,
                status='draft',created_by=user.id,description='Confirmed vacation overpayment return')
            returned.lines=[JournalEntryLine(line_no=1,account_id=bank.accounting_account_id,debit=amount,credit=0),
                JournalEntryLine(line_no=2,account_id=payable,debit=0,credit=amount)]
            db.add(returned);await db.flush();await post_journal_entry(db,company.id,returned.id)
            args=dict(company_id=company.id,calculation_id=new.id,journal_entry_id=returned.id,
                bank_account_id=bank.id,kind='employee',request_key=f'return:{index}',
                document_reference='Confirmed employee bank repayment',actor_id=user.id)
            receipt=await register_sick_return(db,**args)
            assert (await register_sick_return(db,**args)).id==receipt.id
            with pytest.raises(SickReturnError,match='already attributed'):
                await register_sick_return(db,**dict(args,request_key=f'duplicate:{index}'))
            remaining=await sick_settlement_summary(db,company_id=company.id,calculation_id=new.id,as_of=data.correction_date)
            assert remaining['employee_overpayment']==(overpayment-100 if index==0 else 0)
            assert remaining['employee_due']==0
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


@pytest.mark.parametrize('with_severance',[False,True])
@pytest.mark.parametrize('history_daily',[50,100,200])
async def test_compensation_recheck_preserves_paid_final_settlement(employee_engine,with_severance,history_daily,monkeypatch):
    from test_payroll_leave_final_settlement_postgresql import test_termination_compensation_flows_once_through_payroll_and_payment
    from app.models.payroll_leave_compensation import PayrollLeaveCompensation
    await install(employee_engine)
    # Reproduce a historical average-calculation defect only while seeding the
    # original payment. Correction itself uses the real source derivation.
    from app.services import payroll_leave_compensation_service as compensation_service
    derive=compensation_service.derive_compensation_average
    async def defective_average(*args,**kwargs):
        basis=await derive(*args,**kwargs)
        return dict(basis,eligible_earnings=basis['eligible_earnings']*D(100)/history_daily)
    with monkeypatch.context() as seed_patch:
        if history_daily!=100:seed_patch.setattr(compensation_service,'derive_compensation_average',defective_average)
        await test_termination_compensation_flows_once_through_payroll_and_payment(
            employee_engine,with_severance,history_daily=history_daily)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        source=await db.scalar(select(PayrollLeaveCompensation))
        old=await db.scalar(select(PayrollCalculation).where(PayrollCalculation.payroll_input_id==source.payroll_input_id))
        reviewer=User(email=uuid4().hex+'@example.invalid',password_hash='unused',first_name='Independent',last_name='Reviewer',is_active=True)
        db.add(reviewer)
        db.add(AccountingPeriod(company_id=source.company_id,year=2026,month=10,start_date=date(2026,10,1),end_date=date(2026,10,31),status='open',is_locked=False))
        for code,kind in [('94','expense'),('378','asset')]:
            db.add(Account(company_id=source.company_id,code=code,name=code,account_type=kind,
                normal_balance='debit',is_postable=True,is_system=True,is_active=True))
        await db.flush()
        request=await request_leave_correction(db,company_id=source.company_id,actor_id=source.created_by,
            data=LeaveCorrectionRequestCreate(target_kind='compensation',target_id=source.id,
                correction_date=date(2026,10,28),request_key='compensation-review',
                reason='Independent compensation financial review',order_reference='Reviewed termination order'))
        application=await apply_leave_correction(db,company_id=source.company_id,request_id=request.id,actor_id=reviewer.id)
        new=await db.get(PayrollCalculation,application.payroll_calculation_ids[0])
        assert new.gross_amount==old.gross_amount+(D(17)*history_daily-D(1700))
        settlement=await sick_settlement_summary(db,company_id=source.company_id,calculation_id=new.id,as_of=date(2026,10,28))
        assert settlement['employee_due']==max(D(0),(D(17)*history_daily-D(1700))*D('.77'))
        assert settlement['employee_overpayment']==max(D(0),(D(1700)-D(17)*history_daily)*D('.77'))
        if history_daily==200:
            bank=await db.scalar(select(BankAccount).where(BankAccount.company_id==source.company_id))
            payout=await create_payroll_disbursement(db,company_id=source.company_id,
                payroll_calculation_id=new.id,bank_account_id=bank.id,payment_date=date(2026,10,28),
                created_by=reviewer.id,request_key='compensation-difference')
            assert payout.amount==D('1309')
            await generate_and_post_payroll_disbursement_journal(db,company_id=source.company_id,
                payroll_disbursement_id=payout.id,created_by=reviewer.id)
            settled=await sick_settlement_summary(db,company_id=source.company_id,calculation_id=new.id,as_of=date(2026,10,28))
            assert settled['employee_due']==settled['employee_overpayment']==0
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


@pytest.mark.parametrize('new_bucket',[False,True])
async def test_returned_days_increase_already_paid_compensation_once(employee_engine,new_bucket):
    from test_payroll_leave_final_settlement_postgresql import test_termination_compensation_flows_once_through_payroll_and_payment
    from app.models.payroll_leave_compensation import PayrollLeaveCompensation
    from app.services.payroll_leave_balance_service import allocate_leave_days,leave_balances
    await install(employee_engine)
    recorded={}
    async def preceding_leave(db,company,actor,contract,account):
        if new_bucket:
            from test_payroll_social_leave import basis
            from app.schemas.payroll_leave_balance import LeaveAccountCreate,LeaveAccrualCreate
            from app.services.payroll_leave_balance_service import create_leave_account,accrue_leave_days
            account=await create_leave_account(db,company_id=company,actor_id=actor,
                data=LeaveAccountCreate(**dict(basis(),employment_contract_id=contract.id)))
            await accrue_leave_days(db,company_id=company,account_id=account.id,actor_id=actor,
                data=LeaveAccrualCreate(through=date(2026,7,31),source_reference='Verified social entitlement',request_key='social-before'))
        days=10 if new_bucket else 5
        leave=LeaveRequest(company_id=company,employment_contract_id=contract.id,leave_type='social' if new_bucket else 'annual',
            start_date=date(2026,8,1),end_date=date(2026,8,days),status='approved',requested_by=actor,
            approved_by=actor,approved_at=datetime.now(timezone.utc))
        db.add(leave);await db.flush()
        await allocate_leave_days(db,company_id=company,leave_request_id=leave.id,
            allocations=[dict(account_id=account.id,bucket='from_2024',days=D(days))],
            request_key='preceding-leave',source_reference='Documented preceding leave',actor_id=actor)
        source=PayrollVacationCalculation(company_id=company,employment_contract_id=contract.id,
            leave_request_id=leave.id,leave_days=days,reference_period_start=date(2026,2,1),reference_period_end=date(2026,7,31),
            eligible_earnings=10000,eligible_days=100,average_daily_amount=100,vacation_pay_amount=days*100,
            currency_code='UAH',rule_code='UA_ORDER_100',rule_version='verified',calculated_by=actor)
        db.add(source);await db.flush();recorded['vacation_id']=source.id
        if new_bucket:
            await accrue_leave_days(db,company_id=company,account_id=account.id,actor_id=actor,
                data=LeaveAccrualCreate(through=date(2026,9,30),source_reference='Verified final social entitlement',request_key='social-final'))
    await test_termination_compensation_flows_once_through_payroll_and_payment(employee_engine,False,
        before_compensation=preceding_leave,expected_compensation=1700 if new_bucket else 1200)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        source=await db.scalar(select(PayrollLeaveCompensation))
        reviewer=User(email=uuid4().hex+'@example.invalid',password_hash='unused',first_name='Independent',last_name='Reviewer',is_active=True)
        db.add(reviewer)
        db.add(AccountingPeriod(company_id=source.company_id,year=2026,month=10,start_date=date(2026,10,1),end_date=date(2026,10,31),status='open',is_locked=False))
        for code,kind in [('94','expense'),('378','asset')]:db.add(Account(company_id=source.company_id,code=code,name=code,
            account_type=kind,normal_balance='debit',is_postable=True,is_system=True,is_active=True))
        await db.flush()
        request=await request_leave_correction(db,company_id=source.company_id,actor_id=source.created_by,
            data=LeaveCorrectionRequestCreate(target_kind='vacation',target_id=recorded['vacation_id'],
                correction_date=date(2026,10,28),request_key='unused-days',reason='Confirmed partial leave before termination',
                order_reference='Corrected leave order',consent_reference='Signed employee agreement',annual_continuity_reference='Verified prior uninterrupted 14-day main annual part',operation='partial',
                effective_date=date(2026,8,9 if new_bucket else 4),retained_ranges=[dict(start=date(2026,8,1),end=date(2026,8,8 if new_bucket else 3))]))
        await apply_leave_correction(db,company_id=source.company_id,request_id=request.id,actor_id=reviewer.id)
        request=await request_leave_correction(db,company_id=source.company_id,actor_id=source.created_by,
            data=LeaveCorrectionRequestCreate(target_kind='compensation',target_id=source.id,
                correction_date=date(2026,10,28),request_key='additional-days',reason='Compensate returned unused leave days',order_reference='Corrected termination order'))
        app=await apply_leave_correction(db,company_id=source.company_id,request_id=request.id,actor_id=reviewer.id)
        new=await db.get(PayrollCalculation,app.payroll_calculation_ids[0])
        assert new.gross_amount==(23900 if new_bucket else 23400)
        summary=await sick_settlement_summary(db,company_id=source.company_id,calculation_id=new.id,as_of=date(2026,10,28))
        assert summary['employee_due']==154
        balances=await leave_balances(db,company_id=source.company_id,employment_contract_id=source.employment_contract_id,as_of=date(2026,10,28))
        assert all(row['before_2024']==row['from_2024']==0 for row in balances)
        assert (await apply_leave_correction(db,company_id=source.company_id,request_id=request.id,actor_id=reviewer.id)).id==app.id
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


def recall_request(source_id,key='review'):
    return LeaveCorrectionRequestCreate(target_kind='vacation',target_id=source_id,
        correction_date=date(2026,10,28),request_key=key,reason='Confirmed unused leave after payroll',
        order_reference='Corrected leave order',consent_reference='Signed employee agreement',
        annual_continuity_reference='Approved annual schedule: uninterrupted main 14-day part',
        operation='recall',effective_date=date(2026,9,6),recall_ground='industrial_accident',
        retained_ranges=[dict(start=date(2026,9,1),end=date(2026,9,5))])


@pytest.mark.parametrize('already_paid',[D(0),D(200)])
async def test_unpaid_and_partly_paid_leave_only_pays_remaining_difference(employee_engine,already_paid):
    await install(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,reviewer,source,calc,bank,journal=await setup(db)
        if already_paid:
            pay=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=calc.id,
                bank_account_id=bank.id,payment_date=date(2026,9,30),created_by=user.id,amount=already_paid,request_key='partial')
            await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=pay.id,created_by=user.id)
        request=await request_leave_correction(db,company_id=company.id,actor_id=user.id,data=recall_request(source.id))
        app=await apply_leave_correction(db,company_id=company.id,request_id=request.id,actor_id=reviewer.id)
        new_id=app.payroll_calculation_ids[0]
        summary=await sick_settlement_summary(db,company_id=company.id,calculation_id=new_id,as_of=date(2026,10,28))
        assert summary['employee_due']==385-already_paid and summary['employee_overpayment']==0
        pay=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=new_id,
            bank_account_id=bank.id,payment_date=date(2026,10,28),created_by=user.id,request_key='remaining')
        assert pay.amount==385-already_paid
        await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=pay.id,created_by=user.id)
        summary=await sick_settlement_summary(db,company_id=company.id,calculation_id=new_id,as_of=date(2026,10,28))
        assert summary['employee_due']==summary['employee_overpayment']==0
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


async def test_concurrent_review_applies_one_financial_revision(employee_engine):
    import asyncio
    from sqlalchemy import func
    from app.models.payroll_leave_correction import PayrollLeaveCorrection,PayrollLeaveDayCorrection
    await install(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,reviewer,source,calc,_,_=await setup(db)
        request=await request_leave_correction(db,company_id=company.id,actor_id=user.id,data=recall_request(source.id))
        ids=(company.id,reviewer.id,request.id)
        await db.commit()
    async def submit():
        async with AsyncSession(employee_engine,expire_on_commit=False) as db:
            app=await apply_leave_correction(db,company_id=ids[0],actor_id=ids[1],request_id=ids[2])
            await db.commit();return app.id
    results=await asyncio.gather(submit(),submit())
    assert results[0]==results[1]
    async with AsyncSession(employee_engine) as db:
        assert await db.scalar(select(func.count()).select_from(PayrollLeaveCorrection).where(PayrollLeaveCorrection.company_id==ids[0]))==1
        assert await db.scalar(select(func.sum(PayrollLeaveDayCorrection.days)).where(PayrollLeaveDayCorrection.company_id==ids[0]))==5


async def test_second_correction_keeps_first_calculation_source_history(employee_engine):
    from app.services.payroll_average_history_service import _verify_paid_leave_lines
    from app.services.payroll_leave_correction_sources import calculation_leave_snapshot
    await install(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,reviewer,source,calc,_,_=await setup(db)
        request=await request_leave_correction(db,company_id=company.id,actor_id=user.id,data=recall_request(source.id))
        first=await apply_leave_correction(db,company_id=company.id,request_id=request.id,actor_id=reviewer.id)
        data=recall_request(source.id,'second').model_copy(update={'retained_ranges':[
            LeaveCorrectionRange(
                start=date(2026,9,1),end=date(2026,9,3))]})
        request=await request_leave_correction(db,company_id=company.id,actor_id=user.id,data=data)
        second=await apply_leave_correction(db,company_id=company.id,request_id=request.id,actor_id=reviewer.id)
        leave=await db.get(LeaveRequest,source.leave_request_id)
        for application,days in [(first,5),(second,3)]:
            calc_id=application.payroll_calculation_ids[0]
            frozen=await calculation_leave_snapshot(db,company_id=company.id,calculation_id=calc_id,
                target_kind='vacation',target_id=source.id)
            assert len(frozen['used_dates'])==days
            lines=list((await db.scalars(select(PayrollCalculationLine).where(
                PayrollCalculationLine.company_id==company.id,PayrollCalculationLine.payroll_calculation_id==calc_id))).all())
            await _verify_paid_leave_lines(db,company_id=company.id,contract_id=source.employment_contract_id,
                leaves=[leave],lines=lines,month=date(2026,9,1),month_end=date(2026,9,30),calculation_id=calc_id)
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


async def test_failure_after_posting_rolls_back_entire_leave_correction(employee_engine,monkeypatch):
    from sqlalchemy import func
    from app.models.payroll_leave_correction import PayrollLeaveCorrection,PayrollLeaveDayCorrection
    from app.services import accounting_posting
    await install(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,reviewer,source,calc,_,_=await setup(db)
        request=await request_leave_correction(db,company_id=company.id,actor_id=user.id,data=recall_request(source.id))
        company_id,request_id,reviewer_id=company.id,request.id,reviewer.id
        real_post=accounting_posting.post_journal_entry
        async def fail_after_post(*args,**kwargs):
            await real_post(*args,**kwargs)
            raise RuntimeError('Simulated final persistence failure')
        with monkeypatch.context() as patch:
            patch.setattr(accounting_posting,'post_journal_entry',fail_after_post)
            with pytest.raises(RuntimeError,match='final persistence'):
                await apply_leave_correction(db,company_id=company_id,request_id=request_id,actor_id=reviewer_id)
        for model in (PayrollLeaveCorrection,PayrollLeaveDayCorrection):
            assert await db.scalar(select(func.count()).select_from(model).where(model.company_id==company_id))==0
        assert await db.scalar(select(func.count()).select_from(PayrollCalculation).where(PayrollCalculation.company_id==company_id))==1
        applied=await apply_leave_correction(db,company_id=company_id,request_id=request_id,actor_id=reviewer_id)
        assert len(applied.payroll_calculation_ids)==1
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
