"""Opening debts are recognized at cutover; settlement clears GL without VAT."""
from decimal import Decimal
from types import SimpleNamespace
from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.counterparty_open_item import CounterpartyOpenItem
from app.models.payment_settlement_allocation import PaymentSettlementAllocation as Allocation
from app.models.journal_entry import JournalEntry
from app.services.accounting_account_roles import AccountingAccountRole as R
from app.services.event_gl_control_service import EventControlSpec, compare_event_rows


async def reconcile_opening_settlements(db, *, company_id, date_from, date_to, account_id, advance_id, payable):
    items=(await db.execute(select(Allocation).join(CounterpartyOpenItem,
        (CounterpartyOpenItem.company_id==Allocation.company_id)&(CounterpartyOpenItem.id==Allocation.open_item_id))
        .where(Allocation.company_id==company_id,CounterpartyOpenItem.opening_balance_id.is_not(None),
            CounterpartyOpenItem.item_type==('payable' if payable else 'receivable'))
        .order_by(Allocation.id).limit(10001))).scalars().all()
    if len(items)>10000:
        raise ValueError('Opening settlement control exceeds 10000 sources')
    journals=(await db.scalars(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.company_id==company_id,JournalEntry.payment_settlement_allocation_id.in_([i.id for i in items]))
        .execution_options(populate_existing=True))).all()
    grouped=defaultdict(list)
    for journal in journals:
        grouped[journal.payment_settlement_allocation_id].append(journal)
    reports=[]
    balance=Decimal(0)
    for item in items:
        entries=grouped[item.id]
        originals=[entry for entry in entries if entry.reversal_of_id is None]
        original=originals[0] if len(originals)==1 else None
        for reverse in (False,True):
            matching=[entry for entry in entries if (entry.reversal_of_id is not None)==reverse]
            applicable=not reverse or item.reversed_at is not None
            if not applicable and not matching:
                continue
            day=item.reversed_at.date() if reverse and item.reversed_at else item.created_at.date()
            if applicable and day<=date_to:
                balance+=item.amount if reverse else -item.amount
            event=SimpleNamespace(id=item.id,recognition_date=day,
                amount=item.amount if applicable else Decimal(0),reversal_of_id=item.id if reverse else None)
            rows=[(event,entry,line,original if reverse else None) for entry in matching for line in entry.lines or [None]]
            rows=rows or [(event,None,None,original if reverse else None)]
            # Inspect historical sources as well: they contribute to closing balance.
            report=compare_event_rows(spec=EventControlSpec(Allocation,'payment_settlement_allocation_id',
                'recognition_date','amount','debit','credit'),rows=rows,
                account_ids={'debit':account_id if payable else advance_id,'credit':advance_id if payable else account_id},
                focus_account_id=account_id,normal_credit=payable,date_from=date_from,date_to=date_to)
            reports.append(report)
    return reports,balance
