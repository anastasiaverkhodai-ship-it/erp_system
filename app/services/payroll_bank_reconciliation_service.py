"""Match posted payroll payouts in the existing bank reconciliation ledger."""
from decimal import Decimal
from sqlalchemy import select, func
from app.models.bank_statement_line import BankStatementLine
from app.models.bank_statement_reconciliation import (
    BankStatementReconciliation, BankStatementReconciliationActiveLink,
)
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.bank_statement_reconciliation_service import (
    BankStatementReconciliationError, normalize_reconciliation_amount,
    normalize_reconciliation_currency,
)


@serialized_payroll_mutation(BankStatementReconciliationError)
async def reconcile_payroll_disbursement(db, *, company_id, payroll_disbursement_id,
                                         bank_statement_line_id, matched_amount,
                                         currency_code, created_by):
    if created_by <= 0:
        raise BankStatementReconciliationError('Invalid actor')
    line = await db.scalar(select(BankStatementLine).where(
        BankStatementLine.company_id == company_id,
        BankStatementLine.id == bank_statement_line_id,
    ).with_for_update().execution_options(populate_existing=True))
    payout = await db.scalar(select(PayrollDisbursement).where(
        PayrollDisbursement.company_id == company_id,
        PayrollDisbursement.id == payroll_disbursement_id,
    ).execution_options(populate_existing=True))
    if line is None or payout is None:
        raise BankStatementReconciliationError('Statement line or payroll disbursement not found')
    posted = await db.scalar(select(JournalEntry.id).where(
        JournalEntry.company_id == company_id,
        JournalEntry.payroll_disbursement_id == payout.id,
        JournalEntry.reversal_of_id.is_(None),
        JournalEntry.status == JournalEntryStatus.POSTED,
    ))
    if posted is None or payout.cancelled_at is not None:
        raise BankStatementReconciliationError('Only posted payroll disbursement can be reconciled')
    if line.bank_account_id != payout.bank_account_id:
        raise BankStatementReconciliationError('Payroll and statement bank accounts must match')
    if Decimal(line.amount) >= 0:
        raise BankStatementReconciliationError('Payroll requires an outgoing statement line')
    currency = normalize_reconciliation_currency(currency_code)
    if line.currency_code != currency or payout.currency_code != currency:
        raise BankStatementReconciliationError('Payroll and statement currencies must match')
    amount = normalize_reconciliation_amount(amount=matched_amount, currency_code=currency)
    if amount > abs(Decimal(line.amount)):
        raise BankStatementReconciliationError('Matched amount exceeds statement line')
    existing = await db.scalar(select(BankStatementReconciliationActiveLink).where(
        BankStatementReconciliationActiveLink.company_id == company_id,
        BankStatementReconciliationActiveLink.bank_statement_line_id == line.id,
    ))
    if existing is not None:
        event = await db.get(BankStatementReconciliation, existing.reconciliation_id)
        if existing.payroll_disbursement_id == payout.id and event.matched_amount == amount:
            return event
        raise BankStatementReconciliationError('Statement line already reconciled')
    used = await db.scalar(select(func.coalesce(func.sum(BankStatementReconciliation.matched_amount),0))
        .join(BankStatementReconciliationActiveLink,
              BankStatementReconciliationActiveLink.reconciliation_id == BankStatementReconciliation.id)
        .where(BankStatementReconciliationActiveLink.company_id == company_id,
               BankStatementReconciliationActiveLink.payroll_disbursement_id == payout.id))
    if Decimal(used) + amount > Decimal(payout.amount):
        raise BankStatementReconciliationError('Matched amount exceeds payroll disbursement')
    event = BankStatementReconciliation(company_id=company_id,
        bank_statement_line_id=line.id, payment_id=None, payroll_disbursement_id=payout.id,
        matched_amount=amount, currency_code=currency, created_by=created_by)
    db.add(event)
    await db.flush()
    db.add(BankStatementReconciliationActiveLink(company_id=company_id,
        bank_statement_line_id=line.id, payment_id=None, payroll_disbursement_id=payout.id,
        reconciliation_id=event.id))
    await db.flush()
    return event


@serialized_payroll_mutation(BankStatementReconciliationError)
async def unmatch_payroll_disbursement(db, *, company_id, payroll_disbursement_id,
                                      reconciliation_id, reversed_by):
    from app.services.bank_statement_reconciliation_service import reverse_bank_statement_reconciliation
    event = await db.scalar(select(BankStatementReconciliation).where(
        BankStatementReconciliation.company_id == company_id,
        BankStatementReconciliation.id == reconciliation_id,
        BankStatementReconciliation.payroll_disbursement_id == payroll_disbursement_id,
    ))
    if event is None:
        raise BankStatementReconciliationError('Payroll bank reconciliation not found')
    return await reverse_bank_statement_reconciliation(db, company_id=company_id,
        reconciliation_id=reconciliation_id, reversed_by=reversed_by)
