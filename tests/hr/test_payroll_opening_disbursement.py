import os
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_employment_structure import setup
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.bank_account import BankAccount
from app.models.employee import Employee
from app.models.journal_entry import JournalEntry
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_opening import PayrollOpeningDebt
from app.schemas.opening_balance import OpeningBalanceCreate
from app.schemas.payroll_opening import PayrollOpeningAttach
from app.schemas.payroll_disbursement import PayrollDisbursementCreate
from app.services.opening_balance_service import create_opening_balance, post_opening_balance
from app.services.payroll_opening_service import attach_payroll_opening
from app.services.payroll_opening_disbursement_service import create_opening_disbursement
from app.services.payroll_disbursement_service import (
    generate_and_post_payroll_disbursement_journal, reverse_payroll_disbursement_journal,
    cancel_payroll_disbursement, PayrollDisbursementSourceStateError,
)
from app.services.payroll_employee_balance_service import employee_payroll_balance
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def seed_opening(db):
    f=await setup(db)
    employee=await db.get(Employee,f['employee'])
    employee.payment_iban='UA903052992990004149123456789'
    accounts=[Account(company_id=f['company'],code=code,name=code,account_type=kind,
        normal_balance=normal,is_system=True,is_postable=True,is_active=True)
        for code,kind,normal in [('66','liability','credit'),('44','equity','credit'),('311','asset','debit')]]
    db.add_all(accounts)
    db.add(AccountingPeriod(company_id=f['company'],year=2026,month=1,start_date=date(2026,1,1),
        end_date=date(2026,1,31),status='open',is_locked=False))
    await db.flush()
    bank=BankAccount(company_id=f['company'],name='Opening payroll bank',account_number='UA903052992990004149123456789',
        currency_code='UAH',accounting_account_id=accounts[2].id,is_active=True)
    db.add(bank);await db.flush()
    opening=await create_opening_balance(db,f['company'],f['actor'],OpeningBalanceCreate(
        request_key='opening',opening_date=date(2026,1,10),lines=[{'account_id':accounts[1].id,'debit':'1000'},
        {'account_id':accounts[0].id,'credit':'1000'}]))
    await post_opening_balance(db,f['company'],opening.id)
    await attach_payroll_opening(db,company_id=f['company'],opening_balance_id=opening.id,created_by=f['actor'],
        data=PayrollOpeningAttach(request_key='detail',source_reference='Verified opening register',
            debts=[{'employee_id':f['employee'],'net_amount':'1000'}]))
    debt=await db.scalar(select(PayrollOpeningDebt).where(PayrollOpeningDebt.company_id==f['company']))
    return f,bank,debt


async def payout(db,f,bank,debt,key,amount,day=11):
    return await create_opening_disbursement(db,company_id=f['company'],opening_debt_id=debt.id,
        created_by=f['actor'],data=PayrollDisbursementCreate(bank_account_id=bank.id,
            request_key=key,amount=amount,payment_date=date(2026,1,day)))


async def test_opening_partial_payments_retries_reversal_and_dated_balances(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,bank,debt=await seed_opening(db)
        row=await payout(db,f,bank,debt,'first','400')
        assert row.payroll_calculation_id is None
        assert (await payout(db,f,bank,debt,'first','400')).id==row.id
        with pytest.raises(PayrollDisbursementSourceStateError,match='different'):
            await payout(db,f,bank,debt,'first','401')
        # Drafts reserve the amount but do not change the employee's balance.
        with pytest.raises(PayrollDisbursementSourceStateError,match='exceeds'):
            await payout(db,f,bank,debt,'excess','601')
        balance=await employee_payroll_balance(db,company_id=f['company'],employee_id=f['employee'],as_of=date(2026,1,11))
        assert balance['balances'][0]['balance']==D('1000')
        journal=await generate_and_post_payroll_disbursement_journal(db,company_id=f['company'],
            payroll_disbursement_id=row.id,created_by=f['actor'])
        assert journal.status=='posted'
        assert await db.scalar(select(func.count(JournalEntry.id)))==2
        balance=await employee_payroll_balance(db,company_id=f['company'],employee_id=f['employee'],as_of=date(2026,1,11))
        assert balance['balances'][0]['balance']==D('600')
        await reverse_payroll_disbursement_journal(db,company_id=f['company'],payroll_disbursement_id=row.id,
            reversal_date=date(2026,1,15),reversed_by=f['actor'])
        # Reversal on the 15th cannot release capacity on the 12th.
        with pytest.raises(PayrollDisbursementSourceStateError,match='exceeds'):
            await payout(db,f,bank,debt,'backdated','1000',12)
        for day,expected in [(11,D('600')),(15,D('1000'))]:
            balance=await employee_payroll_balance(db,company_id=f['company'],employee_id=f['employee'],as_of=date(2026,1,day))
            assert balance['balances'][0]['balance']==expected
        final=await payout(db,f,bank,debt,'final','1000',15)
        await generate_and_post_payroll_disbursement_journal(db,company_id=f['company'],payroll_disbursement_id=final.id,created_by=f['actor'])
        balance=await employee_payroll_balance(db,company_id=f['company'],employee_id=f['employee'],as_of=date(2026,1,15))
        assert balance['balances'][0]['balance']==0
        assert balance['balances'][0]['accrued_net']==0


async def test_opening_draft_cancel_company_and_date_guards(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,bank,debt=await seed_opening(db)
        with pytest.raises(PayrollDisbursementSourceStateError,match='predate'):
            await payout(db,f,bank,debt,'early','100',9)
        row=await payout(db,f,bank,debt,'draft','1000')
        await cancel_payroll_disbursement(db,company_id=f['company'],payroll_disbursement_id=row.id,cancelled_by=f['actor'])
        with pytest.raises(PayrollDisbursementSourceStateError,match='cancelled'):
            await payout(db,f,bank,debt,'draft','1000')
        assert (await payout(db,f,bank,debt,'replacement','1000')).amount==1000
        other=await setup(db)
        with pytest.raises(PayrollDisbursementSourceStateError,match='not found in company'):
            await create_opening_disbursement(db,company_id=other['company'],opening_debt_id=debt.id,
                created_by=other['actor'],data=PayrollDisbursementCreate(bank_account_id=bank.id,payment_date=date(2026,1,11)))


async def test_opening_payout_uses_bank_reconciliation_and_protects_reversal(employee_engine):
    from app.models.bank_statement import BankStatement
    from app.models.bank_statement_line import BankStatementLine
    from app.services.payroll_bank_reconciliation_service import reconcile_payroll_disbursement,unmatch_payroll_disbursement
    from app.services.accounting_reversal import AccountingReversalError
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,bank,debt=await seed_opening(db)
        payout_row=await payout(db,f,bank,debt,'bank-match','1000')
        await generate_and_post_payroll_disbursement_journal(db,company_id=f['company'],
            payroll_disbursement_id=payout_row.id,created_by=f['actor'])
        statement=BankStatement(company_id=f['company'],bank_account_id=bank.id,external_id='opening-statement')
        db.add(statement);await db.flush()
        line=BankStatementLine(company_id=f['company'],bank_account_id=bank.id,bank_statement_id=statement.id,
            external_line_id='opening-transfer',transaction_date=date(2026,1,11),amount=D('-1000'),currency_code='UAH')
        db.add(line);await db.flush()
        event=await reconcile_payroll_disbursement(db,company_id=f['company'],payroll_disbursement_id=payout_row.id,
            bank_statement_line_id=line.id,matched_amount=D('1000'),currency_code='UAH',created_by=f['actor'])
        with pytest.raises(AccountingReversalError,match='Unmatch'):
            await reverse_payroll_disbursement_journal(db,company_id=f['company'],payroll_disbursement_id=payout_row.id,
                reversal_date=date(2026,1,11),reversed_by=f['actor'])
        await unmatch_payroll_disbursement(db,company_id=f['company'],payroll_disbursement_id=payout_row.id,
            reconciliation_id=event.id,reversed_by=f['actor'])
        reversed_=await reverse_payroll_disbursement_journal(db,company_id=f['company'],payroll_disbursement_id=payout_row.id,
            reversal_date=date(2026,1,11),reversed_by=f['actor'])
        assert reversed_.status=='posted'


async def test_opening_disbursement_migration_roundtrip_and_preservation(employee_engine):
    import importlib.util
    from pathlib import Path
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.autogenerate import compare_metadata
    from app.core.database import Base
    spec=importlib.util.spec_from_file_location('opening_payout_migration',Path('alembic/versions/13d4a7b8c018_opening_payroll_disbursements.py'))
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    async with employee_engine.begin() as connection:
        def roundtrip(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade();migration.upgrade()
            assert compare_metadata(MigrationContext.configure(sync),Base.metadata)==[]
        await connection.run_sync(roundtrip)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,bank,debt=await seed_opening(db)
        row=await payout(db,f,bank,debt,'history','1000')
        def downgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
        with pytest.raises(DBAPIError,match='history must be preserved'):
            async with db.begin_nested():
                await (await db.connection()).run_sync(downgrade)
        assert await db.scalar(select(PayrollDisbursement.id).where(PayrollDisbursement.id==row.id))==row.id


async def test_opening_requests_are_serialized_and_do_not_overpay(employee_engine):
    import asyncio
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,bank,debt=await seed_opening(db)
        bank_id,debt_id=bank.id,debt.id
        await db.commit()
    async def create_request(key):
        async with AsyncSession(employee_engine,expire_on_commit=False) as db:
            try:
                await create_opening_disbursement(db,company_id=f['company'],opening_debt_id=debt_id,
                    created_by=f['actor'],data=PayrollDisbursementCreate(bank_account_id=bank_id,
                        request_key=key,amount='600',payment_date=date(2026,1,11)))
                await db.commit()
                return 'created'
            except PayrollDisbursementSourceStateError:
                await db.rollback()
                return 'rejected'
    assert sorted(await asyncio.wait_for(asyncio.gather(create_request('one'),create_request('two')),20))==['created','rejected']


async def test_opening_disbursement_http_create_list_post_and_access(employee_engine):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from httpx import AsyncClient,ASGITransport
    from app.api.v1.payroll_disbursements import router
    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.models.role import Role
    from app.models.permission import Permission
    from app.models.rbac import role_permissions
    from app.models.user_company_role import UserCompanyRole
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,bank,debt=await seed_opening(db)
        bank_id,debt_id=bank.id,debt.id
        role=Role(name='opening-disbursement-user')
        permissions=[Permission(name=x) for x in ('journal_entries.create','journal_entries.read','journal_entries.approve')]
        db.add_all([role,*permissions]);await db.flush()
        await db.execute(role_permissions.insert(),[dict(role_id=role.id,permission_id=p.id) for p in permissions])
        db.add(UserCompanyRole(user_id=f['actor'],company_id=f['company'],role_id=role.id))
        await db.commit()
        api=FastAPI();api.include_router(router)
        async def session(): yield db
        api.dependency_overrides[get_db]=session
        api.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=f['actor'])
        prefix=f"/companies/{f['company']}/payroll-opening-debts/{debt_id}/disbursements"
        payload=dict(bank_account_id=bank_id,payment_date='2026-01-11',amount='400',request_key='http-opening')
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            response=await client.post(prefix,json=payload)
            assert response.status_code==201,response.text
            saved=response.json()
            assert saved['opening_debt_id']==debt_id and saved['payroll_calculation_id'] is None
            assert (await client.post(prefix,json=payload)).json()==saved
            assert (await client.get(prefix)).json()==[saved]
            posted=await client.post(f"/companies/{f['company']}/payroll-disbursements/{saved['id']}/accounting-journal")
            assert posted.status_code==201,posted.text
            assert (await client.post('/companies/2147483647/payroll-opening-debts/1/disbursements',json=payload)).status_code==403
