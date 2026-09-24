"""Find posted account movements without a supported company-scoped source."""
from sqlalchemy import and_, or_, select
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.document import Document
from app.models.purchase_landed_cost_valuation_event import PurchaseLandedCostValuationEvent


async def unattributed_journals(db, *, company_id, account_id, date_from, date_to, specs, documents=False, landed=False, opening_settlements=False):
    from app.models.payment_settlement_allocation import PaymentSettlementAllocation
    from app.models.counterparty_open_item import CounterpartyOpenItem
    opening_allocations=select(PaymentSettlementAllocation.id).join(CounterpartyOpenItem,
        (CounterpartyOpenItem.company_id==PaymentSettlementAllocation.company_id)
        & (CounterpartyOpenItem.id==PaymentSettlementAllocation.open_item_id)).where(
            PaymentSettlementAllocation.company_id==company_id,CounterpartyOpenItem.opening_balance_id.is_not(None))
    known=[JournalEntry.opening_balance_id.is_not(None)]
    if opening_settlements:
        known.append(JournalEntry.payment_settlement_allocation_id.in_(opening_allocations))
    for spec in specs:
        known.append(getattr(JournalEntry,spec.journal_field).in_(select(spec.model.id).where(spec.model.company_id==company_id)))
    if documents:
        known.append(JournalEntry.document_id.in_(select(Document.id).where(Document.company_id==company_id,Document.status.in_(("posted","reversed")))))
    if landed:
        known.append(select(PurchaseLandedCostValuationEvent.id).where(
            PurchaseLandedCostValuationEvent.company_id==company_id,
            PurchaseLandedCostValuationEvent.journal_entry_id==JournalEntry.id).exists())
    # IS NOT TRUE handles SQL NULL, which must not conceal untyped manual journals.
    return list((await db.scalars(select(JournalEntry.id).join(JournalEntryLine).where(
        JournalEntry.company_id==company_id,JournalEntry.status.in_(('posted','reversed')),
        JournalEntry.entry_date.between(date_from,date_to),JournalEntryLine.account_id==account_id,
        or_(*known).is_not(True)
    ).distinct().order_by(JournalEntry.id))).all())
