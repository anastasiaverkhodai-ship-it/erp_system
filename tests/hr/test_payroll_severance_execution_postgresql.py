"""Atomic financial execution on migrated PostgreSQL; real average, taxes, journals and cash settlement."""
from datetime import date
from decimal import Decimal as D
from uuid import uuid4
import pytest
from sqlalchemy import select,text
from test_payroll_severance_snapshot_service_postgresql import _run_severance_snapshot_service_e2e


@pytest.mark.asyncio
@pytest.mark.parametrize('gross,prepared_draft,tax_mismatch',[(D('13000.00'),False,False),
    (D('10000.00'),False,False),(D('13000.00'),True,False),(D('10000.00'),True,False),
    (D('13000.00'),False,True)])
async def test_paid_severance_recalculation_keeps_cash_and_settles_delta(monkeypatch,gross,prepared_draft,tax_mismatch):
    async def callback(db,company,user,employee,contract,calc,tax):
        from app.models.account import Account
        from app.models.accounting_period import AccountingPeriod
        from app.models.bank_account import BankAccount
        from app.models.employee import Employee
        from app.models.payroll_termination_severance import PayrollTerminationSeverance
        from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
        from app.services.payroll_disbursement_service import create_payroll_disbursement,generate_and_post_payroll_disbursement_journal
        db.add(AccountingPeriod(company_id=company.id,year=2026,month=9,start_date=date(2026,9,1),end_date=date(2026,9,30),status='open',is_locked=False))
        accounts={}
        for code in ('92','94','378','66','64','65','311'):
            kind='asset' if code in ('378','311') else 'expense' if code in ('92','94') else 'liability'
            account=Account(company_id=company.id,code=code,name=code,account_type=kind,
                normal_balance='credit' if kind=='liability' else 'debit',is_postable=True,is_system=True,is_active=True)
            db.add(account);await db.flush();accounts[code]=account.id
        bank=BankAccount(company_id=company.id,name='Payroll',account_number=uuid4().hex,currency_code='UAH',accounting_account_id=accounts['311'],is_active=True)
        db.add(bank)
        stored_employee=await db.get(Employee,employee.id);stored_employee.payment_iban='UA213223130000026007233566001'
        await db.flush()
        await generate_and_post_payroll_journal_entry(db,company_id=company.id,payroll_calculation_id=calc.id,created_by=user.id)
        payout=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=calc.id,bank_account_id=bank.id,
            payment_date=date(2026,9,30),created_by=user.id,request_key='paid-original')
        payment=await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=payout.id,created_by=user.id)
        from app.services.payroll_severance_correction_case_service import open_postpayment_severance_case
        case=await open_postpayment_severance_case(db,company_id=company.id,payroll_calculation_id=calc.id,
            as_of=date(2026,9,30),evidence_date=date(2026,9,30),evidence_reference='Verified changed average earnings',request_key='case-financial',actor_id=user.id)
        from app.services.payroll_severance_calculated_financial import load_calculated_severance_financial
        from app.services.payroll_severance_trusted_classification import TRUSTED_POLICIES
        old=await load_calculated_severance_financial(db,company_id=company.id,calculation_id=calc.id,income_date=contract.end_date,policies=TRUSTED_POLICIES)
        from app.services.payroll_severance_mixed_financial_snapshot import SeveranceMixedFinancialPreview
        from app.services.payroll_severance_net_tax_settlement import SeveranceFinancialSnapshot
        from app.services.payroll_severance_financial_proposal_service import create_severance_financial_proposal
        proposed=SeveranceFinancialSnapshot(gross=gross,personal_income_tax=gross*D('.18')+(D('.01') if tax_mismatch else 0),military_levy=gross*D('.05'),net=gross*D('.77')-(D('.01') if tax_mismatch else 0),employer_contribution=D(0))
        preview=SeveranceMixedFinancialPreview(company_id=company.id,payroll_calculation_id=calc.id,
            severance_source_ids=(case.original_severance_source_id,),ordinary_gross=calc.gross_amount-old.gross,
            financial=old,statutory_totals_reconciled=True)
        proposal=await create_severance_financial_proposal(db,company_id=company.id,correction_case_id=case.id,
            original_preview=preview,proposed_financial=proposed,document_reference='Recalculated average from corrected prior earnings',
            document_date=date(2026,9,30),request_key='proposal-financial',submitted_by=user.id)
        from app.models.user import User
        from app.models.role import Role
        from app.models.permission import Permission
        from app.models.rbac import role_permissions
        from app.models.user_company_role import UserCompanyRole
        reviewer=User(email=uuid4().hex+'@example.invalid',password_hash='unused',first_name='Review',last_name='User',is_active=True)
        role=Role(name=uuid4().hex);db.add_all([reviewer,role]);await db.flush()
        permission=await db.scalar(select(Permission).where(Permission.name=='journal_entries.approve'))
        if permission is None:
            permission=Permission(name='journal_entries.approve');db.add(permission);await db.flush()
        await db.execute(role_permissions.insert(),dict(role_id=role.id,permission_id=permission.id))
        db.add(UserCompanyRole(company_id=company.id,user_id=reviewer.id,role_id=role.id,is_active=True));await db.flush()
        from app.services.payroll_severance_independent_review_service import record_independent_severance_review
        await record_independent_severance_review(db,company_id=company.id,financial_proposal_id=proposal.id,
            reviewer_id=reviewer.id,review_reference='Reviewed source and calculated financial differences',review_date=date(2026,9,30),
            decision='reviewed_pending_statutory',request_key='independent-financial')
        source=await db.get(PayrollTerminationSeverance,case.original_severance_source_id)
        await seed_average_months(db,company.id,user.id,contract.id,gross)
        from app.services.payroll_severance_execution_service import execute_severance_correction
        args=dict(company_id=company.id,proposal_id=proposal.id,correction_date=date(2026,9,30),actor_id=reviewer.id)
        draft=None
        if prepared_draft:
            from app.services.payroll_severance_adjustment_journal_service import create_severance_adjustment_draft
            draft=await create_severance_adjustment_draft(db,company_id=company.id,
                correction_case_id=case.id,correction_date=date(2026,9,30),actor_id=user.id)
        with pytest.raises(ValueError):
            await execute_severance_correction(db,**{**args,'actor_id':user.id})
        if tax_mismatch:
            with pytest.raises(ValueError,match='statutory amount mismatch'):
                await execute_severance_correction(db,**args)
            from app.services.payroll_revision_service import require_current_calculation
            await require_current_calculation(db,calculation=calc,error_type=ValueError)
            assert await db.scalar(select(PayrollTerminationSeverance.id).where(
                PayrollTerminationSeverance.supersedes_id==source.id)) is None
            from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication
            assert await db.scalar(select(PayrollSickCorrectionApplication.id).where(
                PayrollSickCorrectionApplication.company_id==company.id)) is None
            await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            return
        app=await execute_severance_correction(db,**args)
        if draft is not None:
            assert draft.status=='posted'
            assert draft.payroll_calculation_id==app.payroll_calculation_ids[0]
        assert (await execute_severance_correction(db,**args)).id==app.id
        from app.services.payroll_sick_settlement_service import sick_settlement_summary
        summary=await sick_settlement_summary(db,company_id=company.id,calculation_id=app.payroll_calculation_ids[0],as_of=date(2026,9,30))
        delta=(gross-D(12000))*D('.77')
        assert summary['employee_due']==max(delta,0)
        assert summary['employee_overpayment']==max(-delta,0)
        assert payment.status=='posted' and payout.reversed_on is None
        if delta>0:
            extra=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=app.payroll_calculation_ids[0],
                bank_account_id=bank.id,payment_date=date(2026,9,30),created_by=reviewer.id,request_key='corrected-difference')
            assert extra.amount==delta
            from app.services.payroll_severance_payment_service import payment_protected_minimum
            assert await payment_protected_minimum(db,company_id=company.id,payment=extra)==delta
            await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=extra.id,created_by=reviewer.id)
        else:
            from app.models.journal_entry import JournalEntry
            from app.models.journal_entry_line import JournalEntryLine
            from app.services.accounting_posting import post_journal_entry
            from app.services.payroll_sick_return_service import register_sick_return
            receipt=JournalEntry(company_id=company.id,entry_date=date(2026,9,30),status='draft',created_by=reviewer.id)
            receipt.lines=[JournalEntryLine(line_no=1,account_id=accounts['311'],debit=-delta,credit=0),
                JournalEntryLine(line_no=2,account_id=accounts['66'],debit=0,credit=-delta)]
            db.add(receipt);await db.flush();await post_journal_entry(db,company.id,receipt.id)
            await register_sick_return(db,company_id=company.id,calculation_id=app.payroll_calculation_ids[0],
                journal_entry_id=receipt.id,bank_account_id=bank.id,kind='employee',request_key='confirmed-severance-return',
                document_reference='Confirmed voluntary bank return of corrected severance',actor_id=reviewer.id)
        if delta<0:
            # Isolate allocation of the confirmed return for a later protected
            # entitlement; this probe does not create or authorize any payout.
            from types import SimpleNamespace
            from unittest.mock import AsyncMock
            from app.services.payroll_severance_payment_service import payment_protected_minimum
            probe=SimpleNamespace(id=payout.id+1,payroll_calculation_id=app.payroll_calculation_ids[0],
                payment_date=date(2026,9,30),amount=D(1300),withheld_amount=D(0))
            with monkeypatch.context() as context:
                context.setattr('app.services.payroll_severance_payment_service.severance_net',AsyncMock(return_value=D(9000)))
                assert await payment_protected_minimum(db,company_id=company.id,payment=probe)==D(1300)
        final=await sick_settlement_summary(db,company_id=company.id,calculation_id=app.payroll_calculation_ids[0],as_of=date(2026,9,30))
        assert final['employee_due']==final['employee_overpayment']==0
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    await _run_severance_snapshot_service_e2e(exercise_financial_path=True,trusted_policy=True,financial_callback=callback)


async def seed_average_months(db,company_id,actor_id,contract_id,gross):
    """Use production payroll calculation with two fully worked reference months."""
    from calendar import monthrange
    from datetime import datetime,time,timedelta,timezone
    from app.models.time_attendance import WorkSchedule,WorkScheduleDay,EmploymentScheduleAssignment,AttendanceRecord
    from app.models.payroll import PayrollPeriod,PayrollInput,PayrollInputSalarySlice
    from app.models.employee_salary_rate import EmployeeSalaryRate
    from app.services.payroll_calculation_service import calculate_payroll_input
    schedule=WorkSchedule(company_id=company_id,code=uuid4().hex,name='Verified reference schedule',
        timezone='Europe/Kyiv',created_by=actor_id)
    db.add(schedule);await db.flush()
    for weekday in range(1,6):
        db.add(WorkScheduleDay(company_id=company_id,work_schedule_id=schedule.id,weekday=weekday,
            is_working_day=True,start_time=time(9),end_time=time(17),break_minutes=0))
    db.add(EmploymentScheduleAssignment(company_id=company_id,employment_contract_id=contract_id,
        work_schedule_id=schedule.id,effective_from=date(2026,7,1),effective_to=date(2026,8,31),created_by=actor_id))
    for month in (7,8):
        start=date(2026,month,1);end=date(2026,month,monthrange(2026,month)[1])
        days=[start+timedelta(days=n) for n in range(end.day) if (start+timedelta(days=n)).weekday()<5]
        period=PayrollPeriod(company_id=company_id,year=2026,month=month,start_date=start,end_date=end,
            status='finalized',created_by=actor_id,finalized_by=actor_id,finalized_at=datetime.now(timezone.utc))
        db.add(period);await db.flush()
        rate=EmployeeSalaryRate(company_id=company_id,employment_contract_id=contract_id,rate_type='monthly',
            amount=gross,currency_code='UAH',effective_from=start,effective_to=end,created_by=actor_id)
        db.add(rate);await db.flush()
        payroll_input=PayrollInput(company_id=company_id,payroll_period_id=period.id,employment_contract_id=contract_id,
            monthly_norm_minutes=len(days)*480,scheduled_minutes=len(days)*480,worked_minutes=len(days)*480,created_by=actor_id)
        db.add(payroll_input);await db.flush()
        db.add(PayrollInputSalarySlice(company_id=company_id,payroll_input_id=payroll_input.id,salary_rate_id=rate.id,
            salary_rate_type='monthly',salary_rate_amount=gross,currency_code='UAH',effective_from=start,effective_to=end,
            scheduled_minutes=len(days)*480,worked_minutes=len(days)*480))
        for day in days:
            db.add(AttendanceRecord(company_id=company_id,employment_contract_id=contract_id,work_date=day,
                status='present',worked_minutes=480,source='system',created_by=actor_id,
                actual_start_at=datetime.combine(day,time(9),tzinfo=timezone.utc),
                actual_end_at=datetime.combine(day,time(17),tzinfo=timezone.utc)))
        await db.flush()
        calculated=await calculate_payroll_input(db,company_id=company_id,payroll_input_id=payroll_input.id,calculated_by=actor_id)
        assert calculated.gross_amount==gross
