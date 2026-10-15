"""Settle verified opening net debt without creating income or tax again."""
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import aliased
from app.models.bank_account import BankAccount
from app.models.employee import Employee
from app.models.journal_entry import JournalEntry
from app.models.opening_balance import OpeningBalance
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_opening import PayrollOpeningDebt, PayrollOpeningPackage
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_disbursement_service import (
    PayrollDisbursementSourceStateError as StateError,
    PayrollDisbursementNotFoundError as NotFoundError,
)
from app.services.payroll_insurance_funding_service import peak_commitment


async def opening_source(db, *, company_id, opening_debt_id, payment_date, error_type=StateError):
    result = (await db.execute(select(PayrollOpeningDebt, JournalEntry)
        .join(PayrollOpeningPackage,
              (PayrollOpeningPackage.company_id == PayrollOpeningDebt.company_id)
              & (PayrollOpeningPackage.id == PayrollOpeningDebt.package_id))
        .join(OpeningBalance,
              (OpeningBalance.company_id == PayrollOpeningPackage.company_id)
              & (OpeningBalance.id == PayrollOpeningPackage.opening_balance_id))
        .join(JournalEntry,
              (JournalEntry.company_id == OpeningBalance.company_id)
              & (JournalEntry.id == OpeningBalance.journal_entry_id))
        .where(PayrollOpeningDebt.company_id == company_id,
               PayrollOpeningDebt.id == opening_debt_id)
        .execution_options(populate_existing=True))).first()
    if result is None:
        raise error_type('Opening payroll debt not found in company')
    debt, journal = result
    if journal.status != 'posted' or journal.reversal_of_id is not None:
        raise error_type('Opening payroll debt requires its active posted opening journal')
    if payment_date < max(debt.as_of, journal.entry_date):
        raise error_type('Disbursement cannot predate the opening debt')
    if debt.net_amount <= 0:
        raise error_type('Only positive opening payroll debt can be paid to the employee')
    return debt


async def reserved_opening_amount(db, *, company_id, opening_debt_id, payment_date, exclude_id=None):
    original = aliased(JournalEntry)
    reversal = aliased(JournalEntry)
    query = (select(PayrollDisbursement, original.status, original.entry_date, reversal.entry_date)
        .outerjoin(original, (original.company_id == PayrollDisbursement.company_id)
                   & (original.payroll_disbursement_id == PayrollDisbursement.id)
                   & original.reversal_of_id.is_(None))
        .outerjoin(reversal, (reversal.company_id == original.company_id)
                   & (reversal.reversal_of_id == original.id) & (reversal.status == 'posted'))
        .where(PayrollDisbursement.company_id == company_id,
               PayrollDisbursement.opening_debt_id == opening_debt_id))
    if exclude_id is not None:
        query = query.where(PayrollDisbursement.id != exclude_id)
    drafts = Decimal(0)
    movements = []
    for payout, status, posted_date, reversed_date in (await db.execute(query)).all():
        if status in ('posted', 'reversed'):
            # Even cancelled reversed payments remain part of the dated history.
            movements.append((payout.amount + payout.withheld_amount, posted_date, reversed_date))
        elif payout.cancelled_at is None:
            drafts += payout.amount + payout.withheld_amount
    return drafts + peak_commitment(movements, payment_date)


async def require_opening_debt_for_payout(db, *, company_id, disbursement, error_type):
    debt = await opening_source(db, company_id=company_id,
        opening_debt_id=disbursement.opening_debt_id,
        payment_date=disbursement.payment_date, error_type=error_type)
    if debt.employee_id != disbursement.employee_id or debt.currency_code != disbursement.currency_code:
        raise error_type('Opening disbursement employee or currency differs from its source')
    reserved = await reserved_opening_amount(db, company_id=company_id,
        opening_debt_id=debt.id, payment_date=disbursement.payment_date, exclude_id=disbursement.id)
    if reserved + disbursement.amount + disbursement.withheld_amount > debt.net_amount:
        raise error_type('Disbursement exceeds remaining opening payroll debt')


@serialized_payroll_mutation(StateError)
async def create_opening_disbursement(db, *, company_id, opening_debt_id, data, created_by):
    key = data.request_key or f'payroll-opening:{opening_debt_id}'
    existing = await db.scalar(select(PayrollDisbursement).where(
        PayrollDisbursement.company_id == company_id, PayrollDisbursement.request_key == key))
    if existing is not None:
        if (existing.opening_debt_id != opening_debt_id or existing.bank_account_id != data.bank_account_id
                or existing.payment_date != data.payment_date
                or (data.amount is not None and existing.amount != data.amount)):
            raise StateError('Request key contains a different payroll disbursement')
        if existing.cancelled_at is not None:
            raise StateError('Disbursement cancelled; use a new request key')
        return existing
    debt = await opening_source(db, company_id=company_id, opening_debt_id=opening_debt_id,
                                payment_date=data.payment_date)
    employee = await db.scalar(select(Employee).where(
        Employee.company_id == company_id, Employee.id == debt.employee_id))
    if employee is None or not employee.payment_iban:
        raise StateError('Employee payment IBAN is required')
    bank = await db.scalar(select(BankAccount).where(
        BankAccount.company_id == company_id, BankAccount.id == data.bank_account_id))
    if bank is None:
        raise NotFoundError('Bank account not found')
    if not bank.is_active or bank.currency_code != debt.currency_code:
        raise StateError('Active bank account in the opening debt currency is required')
    reserved = await reserved_opening_amount(db, company_id=company_id,
        opening_debt_id=debt.id, payment_date=data.payment_date)
    amount = data.amount if data.amount is not None else debt.net_amount - reserved
    if amount <= 0 or amount + reserved > debt.net_amount:
        raise StateError('Disbursement exceeds remaining opening payroll debt')
    row = PayrollDisbursement(company_id=company_id, request_key=key,
        opening_debt_id=debt.id, payroll_calculation_id=None, employee_id=debt.employee_id,
        bank_account_id=bank.id, employee_iban_snapshot=employee.payment_iban,
        amount=amount.quantize(Decimal("0.01")), currency_code=debt.currency_code, payment_date=data.payment_date,
        created_by=created_by)
    db.add(row)
    await db.flush()
    return row


async def list_opening_disbursements(db, *, company_id, opening_debt_id):
    return list((await db.scalars(select(PayrollDisbursement).where(
        PayrollDisbursement.company_id == company_id,
        PayrollDisbursement.opening_debt_id == opening_debt_id)
        .order_by(PayrollDisbursement.id))).all())
