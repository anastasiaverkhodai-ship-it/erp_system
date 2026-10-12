"""Attribute existing tax payment journals without posting liabilities twice."""
from decimal import Decimal as D
from sqlalchemy import select
from sqlalchemy.orm import selectinload, aliased
from app.models.account import Account
from app.models.bank_account import BankAccount
from app.models.journal_entry import JournalEntry
from app.models.payroll import PayrollCalculation
from app.models.payroll_statutory import PayrollStatutoryResult, PayrollStatutoryResultLine
from app.models.payroll_tax_remittance import PayrollTaxRemittance as Remittance, PayrollTaxRemittanceLine as Line
from app.services.accounting_account_roles import AccountingAccountRole as Role
from app.services.accounting_account_role_resolver import resolve_company_account_roles
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_insurance_funding_service import peak_commitment
from app.services.idempotency_fingerprint_service import generate_request_fingerprint

ROLES = {'personal_income_tax': Role.PAYROLL_PIT_PAYABLE,
         'military_levy': Role.PAYROLL_MILITARY_LEVY_PAYABLE,
         'unified_social_contribution': Role.PAYROLL_USC_PAYABLE}


class TaxRemittanceError(ValueError):
    pass


class TaxRemittanceNotFoundError(TaxRemittanceError):
    pass


async def remittance_record(db, *, company_id, remittance_id):
    row = await db.scalar(select(Remittance).where(Remittance.company_id == company_id,
        Remittance.id == remittance_id))
    if row is None:
        raise TaxRemittanceNotFoundError('Tax remittance not found')
    return row


async def remittance_response(db, row):
    journal = await db.get(JournalEntry, row.journal_entry_id)
    lines = (await db.scalars(select(Line).where(Line.company_id == row.company_id,
        Line.remittance_id == row.id).order_by(Line.payroll_calculation_id))).all()
    return dict(id=row.id, company_id=row.company_id, component=row.component,
        journal_entry_id=row.journal_entry_id, bank_account_id=row.bank_account_id,
        amount=row.amount, document_reference=row.document_reference,
        budget_payment_reference=row.budget_payment_reference, created_by=row.created_by,
        created_at=row.created_at, payment_date=journal.entry_date, status=journal.status,
        allocations=[dict(payroll_calculation_id=x.payroll_calculation_id, amount=x.amount) for x in lines])


async def remittance_commitment(db, *, company_id, calculation_id, component, from_date):
    reversal = aliased(JournalEntry)
    rows = (await db.execute(select(Line.amount, JournalEntry.entry_date, reversal.entry_date)
        .join(Remittance, (Remittance.company_id == Line.company_id) & (Remittance.id == Line.remittance_id))
        .join(JournalEntry, (JournalEntry.company_id == Remittance.company_id) & (JournalEntry.id == Remittance.journal_entry_id))
        .outerjoin(reversal, (reversal.company_id == JournalEntry.company_id) &
            (reversal.reversal_of_id == JournalEntry.id) & (reversal.status == 'posted'))
        .where(Line.company_id == company_id, Line.payroll_calculation_id == calculation_id,
            Remittance.component == component, JournalEntry.status.in_(('posted', 'reversed'))))).all()
    return peak_commitment(rows, from_date)


async def recognized_tax(db, *, company_id, calculation_id, component, payment_date, accounts):
    calculation = await db.scalar(select(PayrollCalculation).where(
        PayrollCalculation.company_id == company_id, PayrollCalculation.id == calculation_id))
    if calculation is None:
        raise TaxRemittanceNotFoundError('Payroll calculation not found in company')
    accrual = await db.scalar(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.company_id == company_id, JournalEntry.payroll_calculation_id == calculation_id,
        JournalEntry.reversal_of_id.is_(None), JournalEntry.status == 'posted'))
    if accrual is None or accrual.entry_date > payment_date:
        raise TaxRemittanceError('Post payroll accrual before attributing its tax payment')
    lines = (await db.scalars(select(PayrollStatutoryResultLine)
        .join(PayrollStatutoryResult,
            (PayrollStatutoryResult.company_id == PayrollStatutoryResultLine.company_id) &
            (PayrollStatutoryResult.id == PayrollStatutoryResultLine.payroll_statutory_result_id))
        .where(PayrollStatutoryResult.company_id == company_id,
            PayrollStatutoryResult.payroll_calculation_id == calculation_id))).all()
    amounts = {key: D(0) for key in ROLES}
    for line in lines:
        if line.component not in amounts or line.currency_code != 'UAH':
            raise TaxRemittanceError('Unsupported payroll statutory component or currency')
        amounts[line.component] += line.amount
    account_id = accounts[ROLES[component]].id
    # PIT and military levy may share account 64. Reconcile the complete
    # liability on that account, while tracking each tax separately below.
    expected = sum((amount for key, amount in amounts.items()
                    if accounts[ROLES[key]].id == account_id), D(0))
    recognized = sum((line.credit - line.debit for line in accrual.lines
                      if line.account_id == account_id), D(0))
    if expected != recognized or amounts[component] <= 0:
        raise TaxRemittanceError('Posted tax liability differs from statutory payroll source')
    return amounts[component]


@serialized_payroll_mutation(TaxRemittanceError)
async def register_tax_remittance(db, *, company_id, data, created_by):
    payload = data.model_dump(mode='json')
    payload['allocations'] = sorted(payload['allocations'], key=lambda x: x['payroll_calculation_id'])
    fingerprint = generate_request_fingerprint(payload)
    old = await db.scalar(select(Remittance).where(Remittance.company_id == company_id,
        Remittance.request_key == data.request_key))
    if old:
        if old.request_fingerprint != fingerprint:
            raise TaxRemittanceError('Request key contains different tax remittance')
        return old
    if await db.scalar(select(Remittance.id).where(Remittance.company_id == company_id,
            Remittance.journal_entry_id == data.journal_entry_id)):
        raise TaxRemittanceError('Payment journal already attributed')
    payment = await db.scalar(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.company_id == company_id, JournalEntry.id == data.journal_entry_id)
        .with_for_update().execution_options(populate_existing=True))
    if payment is None or payment.status != 'posted' or payment.reversal_of_id is not None:
        raise TaxRemittanceError('An original posted bank tax payment is required')
    if any(getattr(payment, column.name) is not None for column in JournalEntry.__table__.columns
           if column.name.endswith('_id') and column.name not in {'company_id','id','reversal_of_id','accounting_rule_id'}):
        raise TaxRemittanceError('Payment is already owned by another business document')
    bank = await db.scalar(select(BankAccount).where(BankAccount.company_id == company_id,
        BankAccount.id == data.bank_account_id, BankAccount.is_active.is_(True)))
    if bank is None or bank.currency_code != 'UAH':
        raise TaxRemittanceError('An active UAH bank account is required')
    bank_account = await db.get(Account, bank.accounting_account_id)
    if bank_account is None or bank_account.account_type != 'asset' or not bank_account.code.startswith('31'):
        raise TaxRemittanceError('Tax payment requires a bank asset account')
    accounts = await resolve_company_account_roles(db, company_id=company_id, roles=tuple(ROLES.values()))
    liability = accounts[ROLES[data.component]].id
    if liability == bank.accounting_account_id:
        raise TaxRemittanceError('Bank and tax payable accounts must be different')
    amount = sum((line.amount for line in data.allocations), D(0))
    debit = credit = D(0)
    for line in payment.lines:
        if line.account_id == liability and line.credit == 0:
            debit += line.debit
        elif line.account_id == bank.accounting_account_id and line.debit == 0:
            credit += line.credit
        elif line.debit or line.credit:
            raise TaxRemittanceError('Payment contains unrelated accounting lines')
    if debit != amount or credit != amount:
        raise TaxRemittanceError('Tax allocations must reconcile to the full bank payment')
    for requested in data.allocations:
        entitled = await recognized_tax(db, company_id=company_id,
            calculation_id=requested.payroll_calculation_id, component=data.component,
            payment_date=payment.entry_date, accounts=accounts)
        used = await remittance_commitment(db, company_id=company_id,
            calculation_id=requested.payroll_calculation_id, component=data.component,
            from_date=payment.entry_date)
        if used + requested.amount > entitled:
            raise TaxRemittanceError('Tax remittance exceeds the unpaid recognized liability')
    row = Remittance(company_id=company_id, journal_entry_id=payment.id, bank_account_id=bank.id,
        component=data.component, amount=amount, request_key=data.request_key,
        request_fingerprint=fingerprint, document_reference=data.document_reference,
        budget_payment_reference=data.budget_payment_reference, created_by=created_by)
    db.add(row); await db.flush()
    db.add_all([Line(company_id=company_id, remittance_id=row.id,
        payroll_calculation_id=x.payroll_calculation_id, amount=x.amount) for x in data.allocations])
    await db.flush()
    return row


async def ensure_no_tax_remittance(db, *, company_id, calculation_id, reversal_date, error_type):
    for component in ROLES:
        if await remittance_commitment(db, company_id=company_id, calculation_id=calculation_id,
                component=component, from_date=reversal_date) > 0:
            raise error_type('Reverse attributed tax remittances before reversing payroll accrual')


@serialized_payroll_mutation(TaxRemittanceError)
async def reverse_tax_remittance(db, *, company_id, remittance_id, reversal_date, reversed_by):
    from app.services.accounting_reversal import reverse_journal_entry
    row = await remittance_record(db, company_id=company_id, remittance_id=remittance_id)
    await reverse_journal_entry(db, company_id, row.journal_entry_id, reversal_date, reversed_by)
    return row
