import os
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_insurance_funding import setup
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.payroll_tax_remittance import PayrollTaxRemittance
from app.schemas.payroll_tax_remittance import TaxRemittanceCreate
from app.services.accounting_posting import post_journal_entry
from app.services.accounting_reversal import reverse_journal_entry, AccountingReversalError
from app.services.payroll_tax_remittance_service import (
    register_tax_remittance, reverse_tax_remittance, remittance_response, TaxRemittanceError,
    TaxRemittanceNotFoundError, remittance_record, remittance_commitment,
)
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def payment(db,company,user,accounts,bank,amount,component='personal_income_tax',day=date(2026,9,30)):
    row=JournalEntry(company_id=company.id,entry_date=day,description='Confirmed payroll tax payment',
        status='draft',created_by=user.id)
    account=accounts['65' if component=='unified_social_contribution' else '64']
    row.lines=[JournalEntryLine(line_no=1,account_id=account,debit=amount,credit=0),
        JournalEntryLine(line_no=2,account_id=bank.accounting_account_id,debit=0,credit=amount)]
    db.add(row);await db.flush()
    return await post_journal_entry(db,company.id,row.id)


def request(journal,bank,calc,amount,key,component='personal_income_tax'):
    return TaxRemittanceCreate(component=component,journal_entry_id=journal.id,bank_account_id=bank.id,
        request_key=key,document_reference='Confirmed bank tax payment',
        budget_payment_reference='Verified budget recipient and payment purpose',
        allocations=[dict(payroll_calculation_id=calc.id,amount=amount)])


@pytest.mark.parametrize('component,total',[('personal_income_tax',D(702)),('military_levy',D(195)),('unified_social_contribution',D(858))])
async def test_partial_tax_payments_no_duplicate_posting_and_reversal_guard(employee_engine,component,total):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        journal=await payment(db,company,user,accounts,bank,total/2,component)
        data=request(journal,bank,calc,total/2,'first',component)
        before=await db.scalar(select(func.count(JournalEntry.id)))
        row=await register_tax_remittance(db,company_id=company.id,data=data,created_by=user.id)
        assert await db.scalar(select(func.count(JournalEntry.id)))==before
        assert (await register_tax_remittance(db,company_id=company.id,data=data,created_by=user.id)).id==row.id
        with pytest.raises(TaxRemittanceError,match='already attributed'):
            await register_tax_remittance(db,company_id=company.id,data=data.model_copy(update={'request_key':'other'}),created_by=user.id)
        with pytest.raises(TaxRemittanceError,match='different tax'):
            await register_tax_remittance(db,company_id=company.id,data=data.model_copy(update={'document_reference':'Changed'}),created_by=user.id)
        with pytest.raises(AccountingReversalError,match='tax remittances'):
            await reverse_journal_entry(db,company.id,accrual.id,date(2026,9,30),user.id)
        excess=await payment(db,company,user,accounts,bank,total,component)
        with pytest.raises(TaxRemittanceError,match='exceeds'):
            await register_tax_remittance(db,company_id=company.id,data=request(excess,bank,calc,total,'excess',component),created_by=user.id)
        await reverse_journal_entry(db,company.id,excess.id,date(2026,9,30),user.id)
        await reverse_tax_remittance(db,company_id=company.id,remittance_id=row.id,reversal_date=date(2026,9,30),reversed_by=user.id)
        assert (await remittance_response(db,row))['status']=='reversed'
        assert await db.scalar(select(func.count(PayrollTaxRemittance.id)))==1
        await reverse_journal_entry(db,company.id,accrual.id,date(2026,9,30),user.id)


async def test_component_and_company_isolation_and_full_payment_reconciliation(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        journal=await payment(db,company,user,accounts,bank,D(200))
        with pytest.raises(TaxRemittanceError,match='exceeds'):
            await register_tax_remittance(db,company_id=company.id,data=request(journal,bank,calc,D(200),'levy','military_levy'),created_by=user.id)
        with pytest.raises(TaxRemittanceError,match='full bank payment'):
            await register_tax_remittance(db,company_id=company.id,data=request(journal,bank,calc,D(199),'amount'),created_by=user.id)
        row=await register_tax_remittance(db,company_id=company.id,data=request(journal,bank,calc,D(200),'pit'),created_by=user.id)
        with pytest.raises(TaxRemittanceNotFoundError):
            await remittance_record(db,company_id=2147483647,remittance_id=row.id)
        with pytest.raises(TaxRemittanceError,match='Company not found'):
            await register_tax_remittance(db,company_id=2147483647,data=request(journal,bank,calc,D(200),'foreign'),created_by=user.id)
        from test_payroll_vacation_postgresql import _seed_identity
        other_company,other_user,_,_=await _seed_identity(db)
        with pytest.raises(TaxRemittanceError,match='original posted'):
            await register_tax_remittance(db,company_id=other_company.id,
                data=request(journal,bank,calc,D(200),'foreign'),created_by=other_user.id)
        second=await payment(db,company,user,accounts,bank,D(195),'military_levy')
        await register_tax_remittance(db,company_id=company.id,data=request(second,bank,calc,D(195),'military','military_levy'),created_by=user.id)
        assert await remittance_commitment(db,company_id=company.id,calculation_id=calc.id,
            component='personal_income_tax',from_date=date(2026,9,30))==200


async def test_later_reversal_does_not_release_earlier_tax_capacity(employee_engine):
    from app.models.accounting_period import AccountingPeriod
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        db.add(AccountingPeriod(company_id=company.id,year=2026,month=10,start_date=date(2026,10,1),
            end_date=date(2026,10,31),status='open',is_locked=False));await db.flush()
        first=await payment(db,company,user,accounts,bank,D(702))
        row=await register_tax_remittance(db,company_id=company.id,data=request(first,bank,calc,D(702),'initial'),created_by=user.id)
        await reverse_tax_remittance(db,company_id=company.id,remittance_id=row.id,reversal_date=date(2026,10,1),reversed_by=user.id)
        earlier=await payment(db,company,user,accounts,bank,D(1))
        with pytest.raises(TaxRemittanceError,match='exceeds'):
            await register_tax_remittance(db,company_id=company.id,data=request(earlier,bank,calc,D(1),'earlier'),created_by=user.id)
        with pytest.raises(AccountingReversalError,match='tax remittances'):
            await reverse_journal_entry(db,company.id,accrual.id,date(2026,9,30),user.id)
        later=await payment(db,company,user,accounts,bank,D(702),day=date(2026,10,1))
        await register_tax_remittance(db,company_id=company.id,data=request(later,bank,calc,D(702),'later'),created_by=user.id)


async def test_funding_http_permissions_and_no_duplicate_posting(employee_engine):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from httpx import AsyncClient,ASGITransport
    from app.api.v1.payroll_tax_remittance import router
    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.models.role import Role
    from app.models.permission import Permission
    from app.models.rbac import role_permissions
    from app.models.user_company_role import UserCompanyRole
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        journal=await payment(db,company,user,accounts,bank,D(400))
        role=Role(name='tax-remittance-user')
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
        prefix=f'/companies/{company.id}/payroll-tax-remittances'
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
            assert (await client.get(f'/companies/2147483647/payroll-tax-remittances/{identifier}')).status_code==403
            reversed_=await client.post(prefix+f'/{identifier}/reverse',json={'reversal_date':'2026-09-30'})
            assert reversed_.status_code==200,reversed_.text
            assert reversed_.json()['status']=='reversed'



async def test_concurrent_tax_attribution_cannot_exceed_liability(employee_engine):
    import asyncio
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        first=await payment(db,company,user,accounts,bank,D(400))
        second=await payment(db,company,user,accounts,bank,D(400))
        inputs=[request(first,bank,calc,D(400),'concurrent-a'),request(second,bank,calc,D(400),'concurrent-b')]
        company_id,user_id,calc_id=company.id,user.id,calc.id
        await db.commit()
    async def register(data):
        async with AsyncSession(employee_engine,expire_on_commit=False) as db:
            try:
                await register_tax_remittance(db,company_id=company_id,data=data,created_by=user_id)
                await db.commit()
                return 'accepted'
            except TaxRemittanceError as exc:
                await db.rollback()
                assert 'exceeds' in str(exc)
                return 'rejected'
    assert sorted(await asyncio.gather(*(register(data) for data in inputs)))==['accepted','rejected']
    async with AsyncSession(employee_engine) as db:
        assert await remittance_commitment(db,company_id=company_id,calculation_id=calc_id,
            component='personal_income_tax',from_date=date(2026,9,30))==400
