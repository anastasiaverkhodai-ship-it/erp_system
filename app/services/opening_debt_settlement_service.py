"""GL-only clearing for debts already recognized by an opening package."""
from sqlalchemy import select
from app.services.payment_journal_service import generate_and_post_settlement_journal_entry, reverse_settlement_journal_entry


async def post_opening_settlement(db, *, company_id, open_item, payment, allocation, adjustment_date, created_by):
    from app.services.payment_settlement_service import PaymentSettlementDataIntegrityError
    from app.models.opening_balance import OpeningBalance
    from app.models.journal_entry import JournalEntry
    opening=await db.get(OpeningBalance,open_item.opening_balance_id)
    journal=await db.scalar(select(JournalEntry).where(JournalEntry.id==opening.journal_entry_id).execution_options(populate_existing=True)) if opening else None
    if opening is None or opening.company_id!=company_id or journal is None or journal.status!='posted' or adjustment_date<opening.opening_date:
        raise PaymentSettlementDataIntegrityError('Opening debt is not active on settlement date')
    await generate_and_post_settlement_journal_entry(db,payment=payment,allocation=allocation,created_by=created_by)


async def reverse_opening_settlement(db, *, company_id, allocation, adjustment_date, reversed_by):
    previous=db.info.get('opening_settlement_reversal')
    db.info['opening_settlement_reversal']=allocation.id
    try:
        await reverse_settlement_journal_entry(db,company_id=company_id,allocation_id=allocation.id,
            reversal_date=adjustment_date,reversed_by=reversed_by)
    finally:
        if previous is None:
            db.info.pop('opening_settlement_reversal',None)
        else:
            db.info['opening_settlement_reversal']=previous
