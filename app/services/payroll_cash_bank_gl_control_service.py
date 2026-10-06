"""Compare independently confirmed payroll payouts with their bank journals."""
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from app.models.bank_account import BankAccount
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.payroll_disbursement import PayrollDisbursement
from app.services.accounting_account_roles import AccountingAccountRole as Role
from app.services.accounting_account_role_resolver import resolve_company_account_roles
from app.services.event_gl_control_service import EventControlSpec, compare_event_rows


async def reconcile_payroll_bank_sources(db, *, company_id, date_from, date_to):
    ranged = aliased(JournalEntry)
    selected = select(PayrollDisbursement.id).where(
        PayrollDisbursement.company_id == company_id,
        or_(PayrollDisbursement.payment_date.between(date_from, date_to),
            PayrollDisbursement.reversed_on.between(date_from, date_to),
            select(ranged.id).where(ranged.company_id == company_id,
                ranged.payroll_disbursement_id == PayrollDisbursement.id,
                ranged.entry_date.between(date_from, date_to)).exists()),
    ).order_by(PayrollDisbursement.id).limit(10001)
    rows = (await db.execute(select(PayrollDisbursement, BankAccount, JournalEntry, JournalEntryLine)
        .outerjoin(BankAccount, and_(BankAccount.company_id == company_id,
                                    BankAccount.id == PayrollDisbursement.bank_account_id))
        .outerjoin(JournalEntry, and_(JournalEntry.company_id == company_id,
                                     JournalEntry.payroll_disbursement_id == PayrollDisbursement.id))
        .outerjoin(JournalEntryLine, JournalEntryLine.journal_entry_id == JournalEntry.id)
        .where(PayrollDisbursement.company_id == company_id, PayrollDisbursement.id.in_(selected))
        .execution_options(populate_existing=True))).all()
    groups = {}
    for payout, bank, entry, line in rows:
        group = groups.setdefault(payout.id, dict(payout=payout, bank=bank, rows=[]))
        if entry is not None:
            group['rows'].append((entry, line))
    if len(groups) > 10000:
        raise ValueError('Too many payroll payouts; request a shorter control range')
    if len(groups) > 10000:
        raise ValueError('Too many payroll sources; request a shorter control range')
    if not groups:
        return []
    accounts = await resolve_company_account_roles(db, company_id=company_id,
                                                    roles=(Role.PAYROLL_NET_PAYABLE,))
    counterpart = accounts[Role.PAYROLL_NET_PAYABLE].id
    sources = []
    spec = EventControlSpec(PayrollDisbursement, 'payroll_disbursement_id',
                            'recognition_date', 'amount', 'debit', 'credit')
    for group in groups.values():
        payout, bank = group['payout'], group['bank']
        if bank is None or bank.currency_code != payout.currency_code:
            raise ValueError('Payroll payout has an invalid bank or currency')
        money_id = bank.accounting_account_id
        if money_id == counterpart:
            raise ValueError('Payroll payable and bank accounts must be distinct')
        originals = {entry.id: entry for entry, _ in group['rows'] if entry.reversal_of_id is None}
        original = next(iter(originals.values())) if len(originals) == 1 else None
        for reversal in (False, True):
            applicable = payout.reversed_on is not None if reversal else payout.confirmed_at is not None
            matching = [(entry, line) for entry, line in group['rows']
                        if (entry.reversal_of_id is not None) == reversal]
            if not applicable and not matching:
                continue
            event = SimpleNamespace(id=payout.id,
                recognition_date=payout.reversed_on if reversal and applicable else payout.payment_date,
                amount=payout.amount if applicable else Decimal(0),
                currency_code=payout.currency_code, reversal_of_id=payout.id if reversal else None)
            transformed = [(event, entry, line, original if reversal else None) for entry, line in matching]
            sources.append(compare_event_rows(spec=spec,
                rows=transformed or [(event, None, None, original if reversal else None)],
                account_ids={'debit': counterpart, 'credit': money_id},
                focus_account_id=money_id, normal_credit=False, date_from=date_from, date_to=date_to))
    return sources


async def reconcile_payroll_advance_sources(db, *, company_id, date_from, date_to):
    """Check advance amounts/accounts and retain dated reversal movements."""
    from app.models.payroll_advance import PayrollAdvance
    ranged = aliased(JournalEntry)
    selected = select(PayrollAdvance.id).where(PayrollAdvance.company_id == company_id,
        or_(PayrollAdvance.payment_date.between(date_from, date_to),
            select(ranged.id).where(ranged.company_id == company_id,
                ranged.payroll_advance_id == PayrollAdvance.id,
                ranged.entry_date.between(date_from, date_to)).exists())).limit(10001)
    rows = (await db.execute(select(PayrollAdvance, BankAccount, JournalEntry, JournalEntryLine)
        .join(BankAccount, and_(BankAccount.company_id == company_id,
            BankAccount.id == PayrollAdvance.bank_account_id))
        .outerjoin(JournalEntry, and_(JournalEntry.company_id == company_id,
            JournalEntry.payroll_advance_id == PayrollAdvance.id))
        .outerjoin(JournalEntryLine, JournalEntryLine.journal_entry_id == JournalEntry.id)
        .where(PayrollAdvance.company_id == company_id, PayrollAdvance.id.in_(selected)))).all()
    groups = {}
    for advance, bank, journal, line in rows:
        group = groups.setdefault(advance.id, [advance, bank, []])
        if journal is not None:
            group[2].append((journal, line))
    if len(groups) > 10000:
        raise ValueError('Too many payroll sources; request a shorter control range')
    if not groups:
        return []
    accounts = await resolve_company_account_roles(db, company_id=company_id, roles=(Role.PAYROLL_NET_PAYABLE,))
    counterpart = accounts[Role.PAYROLL_NET_PAYABLE].id
    spec = EventControlSpec(PayrollAdvance, 'payroll_advance_id', 'recognition_date', 'amount', 'debit', 'credit')
    sources = []
    for advance, bank, entries in groups.values():
        if bank.currency_code != advance.currency_code or bank.accounting_account_id == counterpart:
            raise ValueError('Advance bank account or currency is invalid')
        original = next((journal for journal, _ in entries if journal.reversal_of_id is None), None)
        for reverse in (False, True):
            matching = [(journal, line) for journal, line in entries if (journal.reversal_of_id is not None) == reverse]
            if not matching:
                continue
            journal = matching[0][0]
            posted = journal.status in ('posted', 'reversed')
            event = SimpleNamespace(id=advance.id, recognition_date=journal.entry_date if reverse else advance.payment_date,
                amount=advance.paid_amount if posted else Decimal(0), currency_code=advance.currency_code,
                reversal_of_id=advance.id if reverse else None)
            sources.append(compare_event_rows(spec=spec,
                rows=[(event, entry, line, original if reverse else None) for entry, line in matching],
                account_ids={'debit': counterpart, 'credit': bank.accounting_account_id},
                focus_account_id=bank.accounting_account_id, normal_credit=False, date_from=date_from, date_to=date_to))
    return sources
