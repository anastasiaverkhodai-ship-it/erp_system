import os
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_insurance_evidence import insurance_setup
from test_payroll_average_classification import month
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.payroll import PayrollCalculationLine, PayrollPeriod
from app.models.payroll_statutory import PayrollStatutoryComponent as Component
from app.services.account_types import AccountType, AccountNormalBalance
from app.services.payroll_insurance_evidence_service import review_insurance_evidence
from app.services.payroll_sick_history_service import calculate_sick_from_history
from app.services.payroll_statutory_service import create_statutory_rate, calculate_payroll_statutory_result
from app.services.payroll_accounting_service import (
    generate_and_post_payroll_journal_entry, reverse_payroll_journal_entry, PayrollAccountingReconciliationError,
)
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def sick_accounting_setup(db):
    company,user,contract,leave,reviewer,_,evidence=await insurance_setup(db)
    leave.start_date=evidence.case_start=date(2026,9,1)
    leave.end_date=date(2026,9,10)
    await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,
        reviewed_by=reviewer.id,approved=True)
    sick=await calculate_sick_from_history(db,company_id=company.id,leave_request_id=leave.id,
        insurance_evidence_id=evidence.id,calculated_by=user.id)
    assert sick.employer_amount==sick.insurer_amount==400
    _,calc=await month(db,company,user,contract,3900)
    period=await db.get(PayrollPeriod,calc.payroll_period_id)
    period.month=9;period.start_date=date(2026,9,1);period.end_date=date(2026,9,30)
    line=PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calc.id,line_no=2,
        line_type='sick_pay',quantity=10,rate=80,amount=800,currency_code='UAH',
        source_sick_leave_calculation_id=sick.id)
    db.add(line);await db.flush()
    for component,rate in [(Component.PERSONAL_INCOME_TAX,D('.18')),(Component.MILITARY_LEVY,D('.05')),
                           (Component.UNIFIED_SOCIAL_CONTRIBUTION,D('.22'))]:
        await create_statutory_rate(db,company_id=company.id,component=component,rate=rate,
            effective_from=date(2026,1,1),effective_to=None,actor_user_id=user.id)
    tax=await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,
        actor_user_id=user.id)
    db.add(AccountingPeriod(company_id=company.id,year=2026,month=9,start_date=date(2026,9,1),
        end_date=date(2026,9,30),status='open',is_locked=False))
    accounts={}
    for code in ('92','94','378','66','64','65'):
        kind=AccountType.ASSET if code=='378' else AccountType.EXPENSE if code in ('92','94') else AccountType.LIABILITY
        account=Account(company_id=company.id,code=code,name='Payroll '+code,account_type=kind,
            normal_balance=AccountNormalBalance.CREDIT if kind==AccountType.LIABILITY else AccountNormalBalance.DEBIT,
            is_postable=True,is_system=True,is_active=True)
        db.add(account);await db.flush();accounts[code]=account.id
    return company,user,contract,calc,line,tax,accounts


async def test_sick_funding_posts_receivable_and_expense_and_reverses(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,line,tax,accounts=await sick_accounting_setup(db)
        args=dict(company_id=company.id,payroll_calculation_id=calc.id,created_by=user.id)
        line.amount=801;await db.flush()
        with pytest.raises(PayrollAccountingReconciliationError,match='dated funding'):
            await generate_and_post_payroll_journal_entry(db,**args)
        line.amount=800;await db.flush()
        journal=await generate_and_post_payroll_journal_entry(db,**args)
        debit={code:sum(x.debit for x in journal.lines if x.account_id==identifier) for code,identifier in accounts.items()}
        assert debit['378']==400 and debit['94']==400
        assert debit['92']==3100+tax.employer_contribution_amount
        assert sum(x.debit for x in journal.lines)==sum(x.credit for x in journal.lines)==3900+tax.employer_contribution_amount
        assert (await generate_and_post_payroll_journal_entry(db,**args)).id==journal.id
        reversed_=await reverse_payroll_journal_entry(db,company_id=company.id,payroll_calculation_id=calc.id,
            reversed_by=user.id,reversal_date=date(2026,9,30))
        assert sum(x.credit for x in reversed_.lines if x.account_id==accounts['378'])==400
