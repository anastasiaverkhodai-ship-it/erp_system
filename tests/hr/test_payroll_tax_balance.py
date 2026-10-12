import os
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_tax_remittance import setup,payment,request
from app.models.accounting_period import AccountingPeriod
from app.services.payroll_tax_remittance_service import register_tax_remittance,reverse_tax_remittance,TaxRemittanceNotFoundError
from app.services.payroll_tax_balance_service import payroll_tax_balances
from app.services.accounting_reversal import reverse_journal_entry
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


def component(report,key):
    return next(row for row in report['totals'] if row['component']==key)


async def test_tax_balances_preserve_dates_reversals_and_components(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        db.add(AccountingPeriod(company_id=company.id,year=2026,month=10,start_date=date(2026,10,1),
            end_date=date(2026,10,31),status='open',is_locked=False));await db.flush()
        args=dict(company_id=company.id,as_of=date(2026,9,30))
        empty=await payroll_tax_balances(db,company_id=company.id,as_of=date(2026,8,31))
        assert empty['rows']==[]
        journal=await payment(db,company,user,accounts,bank,D(300))
        row=await register_tax_remittance(db,company_id=company.id,data=request(journal,bank,calc,D(300),'balance'),created_by=user.id)
        report=await payroll_tax_balances(db,**args)
        assert component(report,'personal_income_tax')==dict(component='personal_income_tax',accrued_amount=D(702),paid_amount=D(300),outstanding_amount=D(402))
        assert component(report,'military_levy')['outstanding_amount']==195
        assert component(report,'unified_social_contribution')['outstanding_amount']==858
        assert len(report['rows'])==3
        assert all(x['employee_id']==contract.employee_id and x['payroll_calculation_id']==calc.id for x in report['rows'])
        await reverse_tax_remittance(db,company_id=company.id,remittance_id=row.id,reversal_date=date(2026,10,1),reversed_by=user.id)
        assert await payroll_tax_balances(db,**args)==report
        october=await payroll_tax_balances(db,company_id=company.id,as_of=date(2026,10,1))
        assert component(october,'personal_income_tax')['paid_amount']==0
        assert component(october,'personal_income_tax')['outstanding_amount']==702
        await reverse_journal_entry(db,company.id,accrual.id,date(2026,10,2),user.id)
        assert await payroll_tax_balances(db,**args)==report
        assert await payroll_tax_balances(db,company_id=company.id,as_of=date(2026,10,1))==october
        final=await payroll_tax_balances(db,company_id=company.id,as_of=date(2026,10,2))
        assert final['rows']==[]
        assert all(x['outstanding_amount']==0 for x in final['totals'])
        with pytest.raises(TaxRemittanceNotFoundError):
            await payroll_tax_balances(db,company_id=2147483647,as_of=date(2026,9,30))


async def test_unattributed_payment_is_not_reported_as_allocated_tax(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        await payment(db,company,user,accounts,bank,D(100))
        report=await payroll_tax_balances(db,company_id=company.id,as_of=date(2026,9,30))
        assert component(report,'personal_income_tax')['paid_amount']==0
        assert report['scope']=='posted_payroll_and_attributed_bank_payments'
