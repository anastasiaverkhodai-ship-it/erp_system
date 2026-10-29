"""Actual advance tax payments must reduce the monthly payable exactly once."""
import json
import os
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_insurance_funding import setup
from test_payroll_tax_remittance import payment, request
from app.models.payroll_advance import PayrollAdvance
from app.models.payroll_statutory import PayrollStatutoryRate
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.services.accounting_posting import post_journal_entry
from app.services.payroll_advance_tax_remittance_service import register_advance_tax_remittance, reverse_advance_tax_remittance
from app.services.payroll_tax_remittance_service import register_tax_remittance, remittance_commitment, TaxRemittanceError
from app.services.payroll_tax_balance_service import payroll_tax_balances

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')]


async def seed_advance(db, company, user, contract, calc, accounts, bank):
    # Imported immutable automatic-advance snapshot, authenticated against the
    # same persisted rates as the monthly result. Real postings follow below.
    rates = (await db.scalars(select(PayrollStatutoryRate).where(PayrollStatutoryRate.company_id == company.id))).all()
    snapshot = dict(version=1, employee_id=contract.employee_id, gross_amount='1000.00',
        withholding_amount='230.00', net_amount='770.00', minimum_net_amount='0.00',
        request=dict(company_id=company.id, payroll_period_id=calc.payroll_period_id,
            employment_contract_id=contract.id, bank_account_id=bank.id,
            advance_percentage='40.0000', payment_date='2026-09-20'),
        tax_lines=[dict(component=r.component, source_rate_id=r.id, source_tax_evidence_id=None,
            source_reference=r.source_reference, base_amount='1000.00', rate=str(r.rate),
            amount=str((D(1000)*r.rate).quantize(D('.01'))),
            rate_effective_from=str(r.effective_from), rate_effective_to=None) for r in rates])
    advance = PayrollAdvance(company_id=company.id, payroll_period_id=calc.payroll_period_id,
        employment_contract_id=contract.id, bank_account_id=bank.id, advance_percentage=40,
        calculation_base_amount=2500, calculated_amount=1000, minimum_due_amount=0,
        paid_amount=770, currency_code='UAH', payment_date=date(2026,9,20),
        calculation_snapshot_json=json.dumps(snapshot), created_by=user.id)
    db.add(advance); await db.flush()
    journal = JournalEntry(company_id=company.id, entry_date=advance.payment_date,
        description='Imported confirmed advance', status='draft', created_by=user.id,
        payroll_advance_id=advance.id)
    journal.lines = [JournalEntryLine(line_no=1, account_id=accounts['66'], debit=770, credit=0),
        JournalEntryLine(line_no=2, account_id=bank.accounting_account_id, debit=0, credit=770)]
    db.add(journal); await db.flush()
    await post_journal_entry(db,company.id,journal.id)
    return advance


@pytest.mark.parametrize('component,advance_amount,monthly_total', [
    ('personal_income_tax',D(180),D(702)), ('military_levy',D(50),D(195)),
    ('unified_social_contribution',D(220),D(858))])
async def test_advance_then_monthly_settlement_and_historical_reversal(employee_engine,component,advance_amount,monthly_total):
    from importlib.util import spec_from_file_location, module_from_spec
    from unittest.mock import patch
    from alembic import op
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    spec=spec_from_file_location('advance065','alembic/versions/13d4a7b8c065_advance_tax_settlement.py')
    migration=module_from_spec(spec);spec.loader.exec_module(migration)
    statements=[]
    with patch.object(op,'drop_constraint'),patch.object(op,'create_check_constraint'),patch.object(op,'execute',lambda sql:statements.append(sql)):
        migration.upgrade()
    async with employee_engine.begin() as conn:
        for statement in statements:await conn.execute(text(statement))
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        advance=await seed_advance(db,company,user,contract,calc,accounts,bank)
        journal=await payment(db,company,user,accounts,bank,advance_amount,component,day=advance.payment_date)
        args=dict(company_id=company.id,payroll_advance_id=advance.id,journal_entry_id=journal.id,
            bank_account_id=bank.id,component=component,amount=advance_amount,request_key='advance',
            document_reference='Confirmed bank receipt',budget_payment_reference='Verified destination',created_by=user.id)
        count=await db.scalar(select(func.count(JournalEntry.id)))
        row=await register_advance_tax_remittance(db,**args)
        assert (await register_advance_tax_remittance(db,**args)).id==row.id
        for statement in (
            'UPDATE payroll_advance_tax_remittances SET amount=amount+1 WHERE id=:id',
            'DELETE FROM payroll_advance_tax_remittances WHERE id=:id',
            'TRUNCATE payroll_advance_tax_remittances',
        ):
            with pytest.raises(DBAPIError,match='immutable'):
                async with db.begin_nested():await db.execute(text(statement),dict(id=row.id))
        assert await db.scalar(select(func.count(JournalEntry.id)))==count
        early=await payroll_tax_balances(db,company_id=company.id,as_of=advance.payment_date)
        early_row=next(x for x in early['rows'] if x['component']==component)
        assert early_row['payroll_calculation_id'] is None
        assert early_row['overpaid_amount']==advance_amount
        assert await remittance_commitment(db,company_id=company.id,calculation_id=calc.id,
            component=component,from_date=date(2026,9,30))==advance_amount
        excess=await payment(db,company,user,accounts,bank,monthly_total,component)
        with pytest.raises(TaxRemittanceError,match='exceeds'):
            await register_tax_remittance(db,company_id=company.id,
                data=request(excess,bank,calc,monthly_total,'excess',component),created_by=user.id)
        remaining=monthly_total-advance_amount
        final=await payment(db,company,user,accounts,bank,remaining,component)
        await register_tax_remittance(db,company_id=company.id,
            data=request(final,bank,calc,remaining,'final',component),created_by=user.id)
        result=await payroll_tax_balances(db,company_id=company.id,as_of=date(2026,9,30))
        item=next(x for x in result['rows'] if x['component']==component)
        assert item['paid_amount']==monthly_total and item['outstanding_amount']==0
        await reverse_advance_tax_remittance(db,company_id=company.id,remittance_id=row.id,
            reversal_date=date(2026,9,30),reversed_by=user.id)
        result=await payroll_tax_balances(db,company_id=company.id,as_of=date(2026,9,30))
        item=next(x for x in result['rows'] if x['component']==component)
        assert item['outstanding_amount']==advance_amount
        historical=await payroll_tax_balances(db,company_id=company.id,as_of=date(2026,9,20))
        assert next(x for x in historical['rows'] if x['component']==component)['paid_amount']==advance_amount


async def test_advance_snapshot_cannot_impersonate_another_employee(employee_engine):
    from app.services.payroll_advance_tax_source_proof import (
        verify_automatic_advance_tax_source_in_db, AdvanceTaxSourceProofError)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        advance=await seed_advance(db,company,user,contract,calc,accounts,bank)
        # Detached input cannot redirect ownership even with company-wide rates.
        from types import SimpleNamespace
        candidate=SimpleNamespace(**{column.name:getattr(advance,column.name)
                                     for column in advance.__table__.columns})
        snapshot=json.loads(candidate.calculation_snapshot_json)
        snapshot['employee_id']=contract.employee_id+100000
        candidate.calculation_snapshot_json=json.dumps(snapshot)
        with pytest.raises(AdvanceTaxSourceProofError,match='differs from employment contract'):
            await verify_automatic_advance_tax_source_in_db(db,candidate)
