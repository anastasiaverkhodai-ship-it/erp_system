"""Real DB checks: no silent bypass via advances or a cached monthly result."""
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_employment_structure import setup, contract
from app.models.account import Account, AccountType, AccountNormalBalance
from app.models.bank_account import BankAccount
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.payroll import PayrollPeriod
from app.models.payroll_deduction import PayrollDeductionInstruction
from app.services.accounting_posting import post_journal_entry, AccountingPostingError
from app.services.payroll_advance_service import create_payroll_advance, PayrollAdvanceSourceStateError
from app.services.payroll_automatic_advance_service import create_automatic_payroll_advance
from app.services.payroll_mutation_guard import require_posted_payroll_accrual
from app.services.payroll_deduction_payment_guard import require_payment_without_unresolved_deductions

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')]


async def instruction(db, f, c):
    row = PayrollDeductionInstruction(company_id=f['company'], employment_contract_id=c.id,
        deduction_type='enforcement', method='fixed', fixed_amount=Decimal('100'),
        currency_code='UAH', priority=1, effective_from=date(2026,9,20),
        effective_to=date(2026,9,25), request_key='PAYMENT-GUARD', created_by=f['actor'],
        created_at=datetime.now(timezone.utc))
    db.add(row)
    await db.flush()
    return row


async def test_employee_date_and_company_scope(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f = await setup(db)
        c = await contract(db, f)
        await instruction(db, f, c)
        args = dict(company_id=f['company'], employee_id=f['employee'], error_type=ValueError)
        for day in (19, 26):
            await require_payment_without_unresolved_deductions(db, payment_date=date(2026,9,day), **args)
        for day in (20, 25):
            with pytest.raises(ValueError, match='effective deduction'):
                await require_payment_without_unresolved_deductions(db, payment_date=date(2026,9,day), **args)
        await require_payment_without_unresolved_deductions(db, company_id=2147483647,
            employee_id=f['employee'], payment_date=date(2026,9,20), error_type=ValueError)
        await require_payment_without_unresolved_deductions(db, company_id=f['company'],
            employee_id=2147483647, payment_date=date(2026,9,20), error_type=ValueError)
        # This check precedes any cached monthly result and also covers opening debts.
        for opening_debt in (None, 123):
            payment = SimpleNamespace(cancelled_at=None, employee_id=f['employee'],
                payment_date=date(2026,9,20), opening_debt_id=opening_debt)
            with pytest.raises(ValueError, match='effective deduction'):
                await require_posted_payroll_accrual(db, company_id=f['company'],
                    disbursement=payment, error_type=ValueError)


async def test_new_advances_and_instruction_added_before_posting(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f = await setup(db)
        c = await contract(db, f)
        period = PayrollPeriod(company_id=f['company'], year=2026, month=9,
            start_date=date(2026,9,1), end_date=date(2026,9,30), status='draft', created_by=f['actor'])
        account = Account(company_id=f['company'], code='311', name='Bank', account_type=AccountType.ASSET,
            normal_balance=AccountNormalBalance.DEBIT, is_active=True, is_postable=True)
        db.add_all([period, account]); await db.flush()
        bank = BankAccount(company_id=f['company'], name='Bank', account_number='GUARD-TEST',
            currency_code='UAH', accounting_account_id=account.id, is_active=True)
        db.add(bank); await db.flush()
        args = dict(company_id=f['company'], payroll_period_id=period.id,
            employment_contract_id=c.id, bank_account_id=bank.id, advance_percentage=Decimal('40'),
            payment_date=date(2026,9,20), created_by=f['actor'])
        advance = await create_payroll_advance(db, **args, calculation_base_amount=Decimal('1000'),
            minimum_due_amount=Decimal('0'), currency_code='UAH')
        await instruction(db, f, c)
        journal = JournalEntry(company_id=f['company'], entry_date=advance.payment_date,
            payroll_advance_id=advance.id, created_by=f['actor'], status=JournalEntryStatus.DRAFT)
        db.add(journal); await db.flush()
        with pytest.raises(AccountingPostingError, match='effective deduction'):
            await post_journal_entry(db, f['company'], journal.id)
        assert journal.status == JournalEntryStatus.DRAFT
        # A second employment contract must not bypass an employee's instruction.
        second = await contract(db, f, number='C-2', employment_kind='internal_secondary')
        args['employment_contract_id'] = second.id
        with pytest.raises(PayrollAdvanceSourceStateError, match='effective deduction'):
            await create_payroll_advance(db, **args, calculation_base_amount=Decimal('1000'),
                minimum_due_amount=Decimal('0'), currency_code='UAH')
        with pytest.raises(PayrollAdvanceSourceStateError, match='effective deduction'):
            await create_automatic_payroll_advance(db, **args)
