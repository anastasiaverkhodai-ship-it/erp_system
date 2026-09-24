"""Payment lifecycle to cash/bank GL turnover, including cancellation dates."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from pydantic import BaseModel
from sqlalchemy import and_, or_, select, func
from sqlalchemy.orm import aliased

from app.models.payment import Payment
from app.models.bank_account import BankAccount
from app.models.cash_desk import CashDesk
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.services.accounting_account_roles import AccountingAccountRole as Role
from app.services.accounting_account_role_resolver import resolve_company_account_roles
from app.services.event_gl_control_service import EventControlSpec, EventGlControl, compare_event_rows


class CashBankGlControl(BaseModel):
    company_id: int
    date_from: date
    date_to: date
    expected_amount: Decimal
    posted_amount: Decimal
    difference: Decimal
    matched: bool
    sources: list[EventGlControl]
    unattributed_journal_ids: list[int]


async def reconcile_cash_bank_gl(db, *, company_id, date_from, date_to):
    if company_id <= 0 or date_from > date_to or (date_to-date_from).days > 366:
        raise ValueError("Provide a valid company and a range of at most 367 days")
    accounts = await resolve_company_account_roles(db, company_id=company_id,
        roles=(Role.BANK_CURRENT_UAH, Role.CUSTOMER_ADVANCES, Role.SUPPLIER_ADVANCES))
    ids = {role: account.id for role, account in accounts.items()}
    ranged = aliased(JournalEntry)
    selected = select(Payment.id).where(Payment.company_id == company_id, or_(
        Payment.payment_date.between(date_from,date_to),
        func.date(Payment.cancelled_at).between(date_from,date_to),
        select(ranged.id).where(ranged.company_id == company_id, ranged.payment_id == Payment.id,
                              ranged.entry_date.between(date_from,date_to)).exists()
    )).order_by(Payment.id).limit(10001)
    rows = (await db.execute(select(Payment, BankAccount, CashDesk, JournalEntry, JournalEntryLine)
        .outerjoin(BankAccount,and_(BankAccount.company_id==company_id,BankAccount.id==Payment.bank_account_id))
        .outerjoin(CashDesk,and_(CashDesk.company_id==company_id,CashDesk.id==Payment.cash_desk_id))
        .outerjoin(JournalEntry,and_(JournalEntry.company_id==company_id,JournalEntry.payment_id==Payment.id))
        .outerjoin(JournalEntryLine,JournalEntryLine.journal_entry_id==JournalEntry.id)
        .where(Payment.company_id==company_id,Payment.id.in_(selected))
        .execution_options(populate_existing=True))).all()
    groups = {}
    for payment,bank,cash,entry,line in rows:
        group=groups.setdefault(payment.id,dict(payment=payment,bank=bank,cash=cash,rows=[]))
        if entry is not None:
            group['rows'].append((entry,line))
    if len(groups)>10000:
        raise ValueError("Too many payments; request a shorter control range")
    sources=[]
    for group in groups.values():
        payment=group['payment']
        bank,cash=group['bank'],group['cash']
        if ((payment.bank_account_id is not None and bank is None)
                or (payment.cash_desk_id is not None and cash is None)):
            raise ValueError("Payment has an invalid cash/bank reference")
        if bank is not None and cash is not None:
            raise ValueError("Payment has both cash and bank references")
        destination=bank if bank is not None else cash
        money_id=destination.accounting_account_id if destination is not None else ids[Role.BANK_CURRENT_UAH]
        if destination is not None and destination.currency_code!=payment.currency_code:
            raise ValueError("Payment currency differs from its cash/bank account")
        incoming=payment.direction=='incoming'
        if payment.direction not in ('incoming','outgoing'):
            raise ValueError("Invalid payment direction")
        counterpart=ids[Role.CUSTOMER_ADVANCES if incoming else Role.SUPPLIER_ADVANCES]
        if counterpart==money_id:
            raise ValueError("Cash/bank and counterpart accounts must be distinct")
        confirmed=payment.confirmed_at is not None or payment.status=='confirmed'
        original_entries={entry.id:entry for entry,_ in group['rows'] if entry.reversal_of_id is None}
        original=next(iter(original_entries.values())) if len(original_entries)==1 else None
        for reversal in (False,True):
            applicable=confirmed and (not reversal or payment.status=='cancelled')
            matching=[(entry,line) for entry,line in group['rows'] if (entry.reversal_of_id is not None)==reversal]
            if not applicable and not matching:
                continue
            if reversal and applicable and payment.cancelled_at is None:
                raise ValueError("Cancelled confirmed payment lacks cancellation chronology")
            event_date=payment.cancelled_at.date() if reversal and payment.cancelled_at else payment.payment_date
            event=SimpleNamespace(id=payment.id,recognition_date=event_date,
                amount=payment.amount if applicable else Decimal(0),currency_code=payment.currency_code,
                reversal_of_id=payment.id if reversal else None)
            debit,credit=(money_id,counterpart) if incoming else (counterpart,money_id)
            spec=EventControlSpec(Payment,'payment_id','recognition_date','amount','debit','credit')
            transformed=[(event,entry,line,original if reversal else None) for entry,line in matching]
            if not transformed:
                transformed=[(event,None,None,original if reversal else None)]
            sources.append(compare_event_rows(spec=spec,rows=transformed,
                account_ids={'debit':debit,'credit':credit},focus_account_id=money_id,normal_credit=False,
                date_from=date_from,date_to=date_to))
    money_accounts=select(BankAccount.accounting_account_id).where(BankAccount.company_id==company_id).union(
        select(CashDesk.accounting_account_id).where(CashDesk.company_id==company_id))
    # Expose unexplained manual movements. Opening GL is a separate migration workflow.
    unattributed=list((await db.scalars(select(JournalEntry.id).join(JournalEntryLine).where(
        JournalEntry.company_id==company_id,JournalEntry.status.in_(('posted','reversed')),
        JournalEntry.entry_date.between(date_from,date_to),JournalEntry.payment_id.is_(None),
        JournalEntry.opening_balance_id.is_(None),
        or_(JournalEntryLine.account_id.in_(money_accounts),JournalEntryLine.account_id==ids[Role.BANK_CURRENT_UAH])
    ).distinct().order_by(JournalEntry.id))).all())
    expected=sum((s.expected_amount for s in sources),Decimal(0))
    posted=sum((s.posted_amount for s in sources),Decimal(0))
    if unattributed:
        extra = await db.scalar(select(func.coalesce(func.sum(
            JournalEntryLine.debit-JournalEntryLine.credit),0)).where(
                JournalEntryLine.journal_entry_id.in_(unattributed),
                or_(JournalEntryLine.account_id.in_(money_accounts),
                    JournalEntryLine.account_id==ids[Role.BANK_CURRENT_UAH])))
        posted += Decimal(extra)
    return CashBankGlControl(company_id=company_id,date_from=date_from,date_to=date_to,
        expected_amount=expected,posted_amount=posted,difference=posted-expected,
        matched=all(s.matched for s in sources) and not unattributed,sources=sources,
        unattributed_journal_ids=unattributed)
