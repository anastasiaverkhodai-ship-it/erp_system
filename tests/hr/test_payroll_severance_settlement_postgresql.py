"""Real migrated PostgreSQL: termination source through final bank settlement."""
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.bank_account import BankAccount
from app.models.employee import Employee
from app.models.journal_entry import JournalEntry
from app.models.payroll_disbursement import PayrollDisbursement
from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
from app.services.payroll_disbursement_service import (
    create_payroll_disbursement, generate_and_post_payroll_disbursement_journal,
    reverse_payroll_disbursement_journal, PayrollDisbursementSourceStateError,
)
from app.services.payroll_payslip_service import generate_payroll_payslip
from app.services.payroll_employee_balance_service import employee_payroll_balance
from test_payroll_severance_snapshot_service_postgresql import _run_severance_snapshot_service_e2e


async def finance(db, company, user, employee, contract, calc, tax, *, deductions, advance_amount=D(0)):
    from calendar import monthrange
    month = contract.end_date.month
    last_day = monthrange(2026, month)[1]
    db.add(AccountingPeriod(company_id=company.id,year=2026,month=month,start_date=date(2026,month,1),
        end_date=date(2026,month,last_day),status='open',is_locked=False))
    accounts={}
    for code in ('92','94','378','66','64','65','311'):
        kind='asset' if code in ('378','311') else 'expense' if code in ('92','94') else 'liability'
        row=Account(company_id=company.id,code=code,name='Payroll '+code,account_type=kind,
            normal_balance='credit' if kind=='liability' else 'debit',is_postable=True,is_system=True,is_active=True)
        db.add(row);await db.flush();accounts[code]=row.id
    bank=BankAccount(company_id=company.id,name='Payroll bank',account_number='settlement-bank',currency_code='UAH',
        accounting_account_id=accounts['311'],is_active=True)
    db.add(bank)
    current_employee=await db.get(Employee,employee.id)
    current_employee.payment_iban='UA213223130000026007233566001'
    await db.flush()
    if advance_amount:
        # Existing historical advance, created before this final calculation.
        from app.models.payroll_advance import PayrollAdvance
        from app.services.payroll_advance_service import generate_and_post_payroll_advance_journal
        advance=PayrollAdvance(company_id=company.id,payroll_period_id=calc.payroll_period_id,
            employment_contract_id=contract.id,bank_account_id=bank.id,advance_percentage=100,
            calculation_base_amount=advance_amount,calculated_amount=advance_amount,minimum_due_amount=0,
            paid_amount=advance_amount,currency_code='UAH',payment_date=date(2026,month,10),created_by=user.id)
        db.add(advance);await db.flush()
        await generate_and_post_payroll_advance_journal(db,company_id=company.id,payroll_advance_id=advance.id,created_by=user.id)
    args=dict(company_id=company.id,payroll_calculation_id=calc.id,created_by=user.id)
    journal=await generate_and_post_payroll_journal_entry(db,**args)
    assert journal.entry_date==contract.end_date
    assert sum(x.debit for x in journal.lines)==sum(x.credit for x in journal.lines)==D('13268.80')
    assert (await generate_and_post_payroll_journal_entry(db,**args)).id==journal.id
    from test_payroll_tax_remittance import payment as tax_payment, request as tax_request
    from app.services.payroll_tax_remittance_service import register_tax_remittance
    from app.services.payroll_tax_balance_service import payroll_tax_balances
    for component,amount in [('personal_income_tax',D('2347.20')),('military_levy',D('652')),('unified_social_contribution',D('228.80'))]:
        tax_journal=await tax_payment(db,company,user,accounts,bank,amount,component,day=contract.end_date)
        data=tax_request(tax_journal,bank,calc,amount,component,component)
        remitted=await register_tax_remittance(db,company_id=company.id,data=data,created_by=user.id)
        assert (await register_tax_remittance(db,company_id=company.id,data=data,created_by=user.id)).id==remitted.id
    taxes=await payroll_tax_balances(db,company_id=company.id,as_of=contract.end_date)
    assert all(x['outstanding_amount']==0 for x in taxes['totals'])
    if advance_amount > D('800.80'):
        with pytest.raises(PayrollDisbursementSourceStateError,match='Advance offset cannot consume protected severance'):
            await create_payroll_disbursement(db,bank_account_id=bank.id,payment_date=contract.end_date,**args)
        return
    if deductions:
        from test_payroll_deduction_execution_postgresql import authorize, request as deduction_request
        def request(*args, **kwargs):
            return deduction_request(*args, **kwargs).model_copy(update={"payment_date": date(2026,month,last_day)})
        from app.services.payroll_deduction_execution_service import execute_payment
        await authorize(db,company,user,contract,D('1000'),migrated=True)
        # Caller supplies zero protection; source-derived severance overrides it.
        first=await execute_payment(db,company_id=company.id,actor_id=user.id,
            data=request(calc,bank,'protected-first',amount=D('5000')).model_copy(update={'payment_date':contract.end_date}))
        assert first.protected_amount==5000 and first.withheld_amount==0
        final_request=request(calc,bank,'protected-final',amount=D('5040.80'))
        final=await execute_payment(db,company_id=company.id,actor_id=user.id,data=final_request)
        assert final.protected_amount==D('4240')
        assert final.withheld_amount==D('160.16') # only ordinary net 800.80 * 20%
        assert (await execute_payment(db,company_id=company.id,actor_id=user.id,data=final_request)).id==final.id
    else:
        payment=await create_payroll_disbursement(db,bank_account_id=bank.id,payment_date=contract.end_date,**args)
        assert payment.amount==tax.net_amount-advance_amount==D('10040.80')-advance_amount
        posting=await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=payment.id,created_by=user.id)
        assert sum(x.debit for x in posting.lines)==D('10040.80')-advance_amount
    slip=await generate_payroll_payslip(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
    assert slip.gross_amount==D('13040') and slip.net_amount==D('10040.80')
    balance=await employee_payroll_balance(db,company_id=company.id,employee_id=employee.id,as_of=date(2026,month,last_day))
    assert balance['complete'] and balance['balances'][0]['balance']==0
    with pytest.raises(PayrollDisbursementSourceStateError,match='remaining'):
        if deductions:
            await execute_payment(db,company_id=company.id,actor_id=user.id,
                data=request(calc,bank,'overpay',amount=D('1')))
        else:
            await create_payroll_disbursement(db,bank_account_id=bank.id,payment_date=contract.end_date,amount=D('1'),request_key='overpay',**args)
    if deductions:
        from app.services.accounting_reversal import AccountingReversalError
        with pytest.raises(AccountingReversalError,match='later severance payments'):
            await reverse_payroll_disbursement_journal(db,company_id=company.id,
                payroll_disbursement_id=first.disbursement_id,reversal_date=date(2026,month,last_day),reversed_by=user.id)
        await reverse_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=final.disbursement_id,reversal_date=date(2026,month,last_day),reversed_by=user.id)
        if contract.end_date.day < 30:
            with pytest.raises(AccountingReversalError,match='later severance payments'):
                await reverse_payroll_disbursement_journal(db,company_id=company.id,
                    payroll_disbursement_id=first.disbursement_id,reversal_date=date(2026,month,last_day-1),reversed_by=user.id)
        replacement=await execute_payment(db,company_id=company.id,actor_id=user.id,
            data=request(calc,bank,'replacement',amount=D('5040.80')))
        assert replacement.protected_amount==D('4240') and replacement.withheld_amount==D('160.16')
        await reverse_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=replacement.disbursement_id,reversal_date=date(2026,month,last_day),reversed_by=user.id)
        await reverse_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=first.disbursement_id,reversal_date=date(2026,month,last_day),reversed_by=user.id)
    else:
        await reverse_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=payment.id,reversal_date=date(2026,month,last_day),reversed_by=user.id)
    balance=await employee_payroll_balance(db,company_id=company.id,employee_id=employee.id,as_of=date(2026,month,last_day))
    assert balance['complete'] and balance['balances'][0]['balance']==D('10040.80')-advance_amount


@pytest.mark.asyncio
@pytest.mark.parametrize('deductions',[False,True])
@pytest.mark.parametrize('termination_day',[15,30])
async def test_severance_final_settlement(deductions, termination_day):
    async def callback(*args):
        await finance(*args,deductions=deductions)
    await _run_severance_snapshot_service_e2e(exercise_financial_path=True,trusted_policy=True,financial_callback=callback,termination_day=termination_day)


@pytest.mark.asyncio
async def test_severance_classification_http_permission_retry_and_company_scope():
    async def callback(db,company,user,employee,contract,calc,tax):
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport
        from types import SimpleNamespace
        from app.api.v1.payroll_statutory import router
        from app.api.deps import get_current_user
        from app.core.database import get_db
        from app.models.role import Role
        from app.models.permission import Permission
        from app.models.rbac import role_permissions
        from app.models.user_company_role import UserCompanyRole
        api=FastAPI();api.include_router(router)
        async def session(): yield db
        api.dependency_overrides[get_db]=session
        api.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=user.id)
        url=f'/companies/{company.id}/payroll-calculations/{calc.id}/severance-classification'
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            assert (await client.post(url)).status_code==403
            role=Role(name=f'severance-http-{company.id}');db.add(role);await db.flush()
            permission=await db.scalar(select(Permission).where(Permission.name=='employees.manage'))
            if permission is None:
                permission=Permission(name='employees.manage');db.add(permission);await db.flush()
            await db.execute(role_permissions.insert(),dict(role_id=role.id,permission_id=permission.id))
            db.add(UserCompanyRole(company_id=company.id,user_id=user.id,role_id=role.id,is_active=True))
            await db.flush()
            first=await client.post(url)
            assert first.status_code==200,first.text
            assert first.json()['income_code_4df']=='127'
            second=await client.post(url)
            assert second.status_code==200 and second.json()==first.json()
            assert (await client.post(url.replace(f'/companies/{company.id}/','/companies/2147483647/'))).status_code==403
    await _run_severance_snapshot_service_e2e(exercise_financial_path=True,trusted_policy=True,financial_callback=callback)


@pytest.mark.asyncio
@pytest.mark.parametrize('advance_amount',[D('300'),D('801')])
async def test_severance_advance_offset(advance_amount):
    async def callback(*args):
        await finance(*args,deductions=False,advance_amount=advance_amount)
    await _run_severance_snapshot_service_e2e(exercise_financial_path=True,trusted_policy=True,financial_callback=callback)


@pytest.mark.asyncio
async def test_current_policy_october_final_settlement():
    async def callback(*args):
        await finance(*args, deductions=True)
    await _run_severance_snapshot_service_e2e(
        exercise_financial_path=True, trusted_policy=True, financial_callback=callback,
        termination_day=22, termination_month=10,
    )
