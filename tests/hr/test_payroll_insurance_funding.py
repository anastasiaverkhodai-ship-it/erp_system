import os
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_sick_accounting import sick_accounting_setup
from app.models.account import Account
from app.models.bank_account import BankAccount
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.payroll_insurance_funding import PayrollInsuranceFunding
from app.services.account_types import AccountType,AccountNormalBalance
from app.services.accounting_posting import post_journal_entry
from app.services.accounting_reversal import reverse_journal_entry,AccountingReversalError
from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
from app.schemas.payroll_insurance_funding import InsuranceFundingCreate
from app.services.payroll_insurance_funding_service import (
    register_insurance_funding,reverse_insurance_funding,funding_response,InsuranceFundingError,
)
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def setup(db):
    company,user,contract,calc,_,_,accounts=await sick_accounting_setup(db)
    accrual=await generate_and_post_payroll_journal_entry(db,company_id=company.id,payroll_calculation_id=calc.id,created_by=user.id)
    account=Account(company_id=company.id,code='313',name='Insurance special account',account_type=AccountType.ASSET,
        normal_balance=AccountNormalBalance.DEBIT,is_postable=True,is_system=True,is_active=True)
    db.add(account);await db.flush()
    bank=BankAccount(company_id=company.id,name='Insurance special account',account_number='test-insurance-account',
        currency_code='UAH',accounting_account_id=account.id,is_active=True)
    db.add(bank);await db.flush()
    return company,user,contract,calc,accrual,accounts,bank


async def receipt(db,company,user,accounts,bank,amount):
    row=JournalEntry(company_id=company.id,entry_date=date(2026,9,30),description='Actual insurance receipt',
        status='draft',created_by=user.id)
    row.lines=[JournalEntryLine(line_no=1,account_id=bank.accounting_account_id,debit=amount,credit=0),
        JournalEntryLine(line_no=2,account_id=accounts['378'],debit=0,credit=amount)]
    db.add(row);await db.flush()
    return await post_journal_entry(db,company.id,row.id)


def request(journal,bank,calc,amount,key):
    return InsuranceFundingCreate(journal_entry_id=journal.id,bank_account_id=bank.id,request_key=key,
        document_reference='PFU funding decision and statement',separate_account_reference='Bank special-account agreement',
        allocations=[dict(payroll_calculation_id=calc.id,amount=amount)])


async def test_partial_receipts_limits_retry_reversal_and_source_guard(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        first_journal=await receipt(db,company,user,accounts,bank,D(300))
        data=request(first_journal,bank,calc,D(300),'first')
        before=await db.scalar(select(func.count(JournalEntry.id)))
        first=await register_insurance_funding(db,company_id=company.id,data=data,created_by=user.id)
        assert await db.scalar(select(func.count(JournalEntry.id)))==before
        assert (await register_insurance_funding(db,company_id=company.id,data=data,created_by=user.id)).id==first.id
        with pytest.raises(InsuranceFundingError,match='already attributed'):
            await register_insurance_funding(db,company_id=company.id,data=data.model_copy(update={'request_key':'duplicate'}),created_by=user.id)
        excess=await receipt(db,company,user,accounts,bank,D(200))
        with pytest.raises(InsuranceFundingError,match='exceeds'):
            await register_insurance_funding(db,company_id=company.id,data=request(excess,bank,calc,D(200),'excess'),created_by=user.id)
        await reverse_journal_entry(db,company.id,excess.id,date(2026,9,30),user.id)
        with pytest.raises(AccountingReversalError,match='insurance funding'):
            await reverse_journal_entry(db,company.id,accrual.id,date(2026,9,30),user.id)
        final_journal=await receipt(db,company,user,accounts,bank,D(100))
        final=await register_insurance_funding(db,company_id=company.id,data=request(final_journal,bank,calc,D(100),'final'),created_by=user.id)
        assert (await funding_response(db,first))['amount']==300
        await reverse_insurance_funding(db,company_id=company.id,funding_id=first.id,reversal_date=date(2026,9,30),reversed_by=user.id)
        assert (await funding_response(db,first))['status']=='reversed'
        assert await db.scalar(select(func.count(PayrollInsuranceFunding.id)))==2
        await reverse_insurance_funding(db,company_id=company.id,funding_id=final.id,reversal_date=date(2026,9,30),reversed_by=user.id)
        await reverse_journal_entry(db,company.id,accrual.id,date(2026,9,30),user.id)


async def test_receipt_cannot_be_reversed_while_payroll_is_paid(employee_engine):
    from app.models.employee import Employee
    from app.services.payroll_disbursement_service import create_payroll_disbursement,generate_and_post_payroll_disbursement_journal
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        journal=await receipt(db,company,user,accounts,bank,D(400))
        await register_insurance_funding(db,company_id=company.id,data=request(journal,bank,calc,D(400),'funded'),created_by=user.id)
        employee=await db.get(Employee,contract.employee_id)
        employee.payment_iban='UA213223130000026007233566001';await db.flush()
        payout=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=calc.id,
            bank_account_id=bank.id,payment_date=date(2026,9,30),created_by=user.id,amount=D(100),request_key='partial')
        await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=payout.id,created_by=user.id)
        with pytest.raises(AccountingReversalError,match='dependent payroll payouts'):
            await reverse_journal_entry(db,company.id,journal.id,date(2026,9,30),user.id)


async def test_later_reversal_does_not_release_past_funding_capacity(employee_engine):
    from app.models.accounting_period import AccountingPeriod
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        db.add(AccountingPeriod(company_id=company.id,year=2026,month=10,start_date=date(2026,10,1),
            end_date=date(2026,10,31),status='open',is_locked=False));await db.flush()
        original=await receipt(db,company,user,accounts,bank,D(400))
        funding=await register_insurance_funding(db,company_id=company.id,data=request(original,bank,calc,D(400),'original'),created_by=user.id)
        await reverse_insurance_funding(db,company_id=company.id,funding_id=funding.id,
            reversal_date=date(2026,10,5),reversed_by=user.id)
        backdated=await receipt(db,company,user,accounts,bank,D(400))
        with pytest.raises(InsuranceFundingError,match='exceeds'):
            await register_insurance_funding(db,company_id=company.id,data=request(backdated,bank,calc,D(400),'backdated'),created_by=user.id)
        with pytest.raises(AccountingReversalError,match='insurance funding'):
            await reverse_journal_entry(db,company.id,accrual.id,date(2026,9,30),user.id)


async def test_funding_http_permissions_and_no_duplicate_posting(employee_engine):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from httpx import AsyncClient,ASGITransport
    from app.api.v1.payroll_insurance_funding import router
    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.models.role import Role
    from app.models.permission import Permission
    from app.models.rbac import role_permissions
    from app.models.user_company_role import UserCompanyRole
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        journal=await receipt(db,company,user,accounts,bank,D(400))
        role=Role(name='insurance-funding-user')
        permissions=[Permission(name=x) for x in ('journal_entries.create','journal_entries.read','journal_entries.reverse','employees.manage')]
        db.add_all([role,*permissions]);await db.flush()
        await db.execute(role_permissions.insert(),[dict(role_id=role.id,permission_id=p.id) for p in permissions])
        db.add(UserCompanyRole(user_id=user.id,company_id=company.id,role_id=role.id))
        await db.commit()
        api=FastAPI();api.include_router(router)
        async def session(): yield db
        api.dependency_overrides[get_db]=session
        actor=SimpleNamespace(id=user.id)
        api.dependency_overrides[get_current_user]=lambda:actor
        prefix=f'/companies/{company.id}/payroll-insurance-funding'
        payload=request(journal,bank,calc,D(400),'http').model_dump(mode='json')
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            invalid=dict(payload,allocations=[dict(payroll_calculation_id=calc.id,amount='399.00')])
            assert (await client.post(prefix,json=invalid)).status_code==409
            result=await client.post(prefix,json=payload)
            assert result.status_code==201,result.text
            saved=result.json();identifier=saved['id']
            assert (await client.post(prefix,json=payload)).json()==saved
            equivalent=dict(payload,allocations=[dict(payroll_calculation_id=payload['allocations'][0]['payroll_calculation_id'],amount='400')])
            assert (await client.post(prefix,json=equivalent)).json()==saved
            assert (await client.get(prefix+f'/{identifier}')).json()==saved
            assert (await client.get(prefix)).json()==[saved]
            assert (await client.get(f'/companies/2147483647/payroll-insurance-funding/{identifier}')).status_code==403
            reversed_=await client.post(prefix+f'/{identifier}/reverse',json={'reversal_date':'2026-09-30'})
            assert reversed_.status_code==200,reversed_.text
            assert reversed_.json()['status']=='reversed'


async def test_unattributed_receipt_uses_company_lock_before_reversal(employee_engine):
    import asyncio
    from app.models.company import Company
    from app.services.payroll_mutation_guard import lock_payroll_journal_company
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        journal=await receipt(db,company,user,accounts,bank,D(400))
        company_id,journal_id=company.id,journal.id
        await db.commit()
    async with AsyncSession(employee_engine) as registration, AsyncSession(employee_engine) as reversal:
        await registration.scalar(select(Company.id).where(Company.id==company_id).with_for_update())
        task=asyncio.create_task(lock_payroll_journal_company(reversal,company_id,journal_id))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task),0.2)
            await registration.rollback()
            await asyncio.wait_for(task,5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
            await reversal.rollback()
