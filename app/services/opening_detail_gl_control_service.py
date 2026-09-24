"""Independently compare imported stock/debt principal with its opening GL leg."""
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.opening_balance import OpeningBalance
from app.models.opening_balance_detail import OpeningBalanceDetail
from app.models.counterparty_open_item import CounterpartyOpenItem
from app.models.document import Document
from app.models.journal_entry import JournalEntry
from app.services.event_gl_control_service import EventGlControl, EventGlIssue

ZERO=Decimal(0)


async def reconcile_opening_detail_gl(db, *, company_id, date_to, account_id, family):
    packages=(await db.execute(select(OpeningBalanceDetail,OpeningBalance).join(OpeningBalance,
        (OpeningBalance.company_id==OpeningBalanceDetail.company_id)
        &(OpeningBalance.id==OpeningBalanceDetail.opening_balance_id)).where(
        OpeningBalanceDetail.company_id==company_id,OpeningBalance.opening_date<=date_to)
        .order_by(OpeningBalance.id).limit(10001))).all()
    if len(packages)>10000:
        raise ValueError('Opening control exceeds 10000 packages')
    reports=[]
    correction=ZERO
    for package,opening in packages:
        issues=[]
        def issue(code,journal=None):
            issues.append(EventGlIssue(source_type='opening_balance_details',source_id=package.id,
                journal_id=journal.id if journal else None,code=code))
        if family=='inventory':
            document=await db.scalar(select(Document).options(selectinload(Document.lines)).where(
                Document.company_id==company_id,Document.id==package.stock_document_id)) if package.stock_document_id else None
            principal=sum(((line.quantity*line.price).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                for line in document.lines),ZERO) if document else ZERO
            if package.stock_document_id and (document is None or document.status not in ('posted','reversed')):
                issue('invalid_opening_stock_document')
        else:
            items=(await db.scalars(select(CounterpartyOpenItem).where(CounterpartyOpenItem.company_id==company_id,
                CounterpartyOpenItem.opening_balance_id==opening.id,
                CounterpartyOpenItem.item_type==('payable' if family=='ap' else 'receivable')))).all()
            principal=sum((item.original_amount for item in items),ZERO)
            if any(item.currency_code!='UAH' for item in items):
                issue('unsupported_currency')
        if not principal.is_finite() or principal<0:
            raise ValueError('Invalid opening detail amount')
        entries=(await db.scalars(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
            JournalEntry.company_id==company_id,JournalEntry.opening_balance_id==opening.id)
            .execution_options(populate_existing=True))).all()
        originals=[entry for entry in entries if entry.reversal_of_id is None]
        reversals=[entry for entry in entries if entry.reversal_of_id is not None]
        if len(originals)!=1 or originals[0].id!=opening.journal_entry_id:
            issue('opening_journal_count')
        original=originals[0] if len(originals)==1 else None
        if len(reversals)!=(1 if original and original.status=='reversed' else 0):
            issue('opening_reversal_count')
        expected=principal
        if any(entry.entry_date<=date_to for entry in reversals):
            expected-=principal
        actual=ZERO
        for entry in entries:
            reverse=entry.reversal_of_id is not None
            if entry.status not in ('posted','reversed'):
                issue('journal_not_posted',entry)
            if (not reverse and entry.entry_date!=opening.opening_date) or (reverse and entry.reversal_of_id!=opening.journal_entry_id):
                issue('opening_journal_provenance',entry)
            debit=sum((line.debit for line in entry.lines if line.account_id==account_id),ZERO)
            credit=sum((line.credit for line in entry.lines if line.account_id==account_id),ZERO)
            wants_credit=(family=='ap')!=reverse
            wanted=(ZERO,principal) if wants_credit else (principal,ZERO)
            if (debit,credit)!=wanted:
                issue('opening_detail_gl_mismatch',entry)
            if entry.entry_date<=date_to and entry.status in ('posted','reversed'):
                actual+=credit-debit if family=='ap' else debit-credit
        correction+=expected-actual
        reports.append(EventGlControl(source_type='opening_balance_details',event_count=1,
            expected_amount=expected,posted_amount=actual,difference=actual-expected,
            matched=not issues and actual==expected,issues=issues))
    return reports,correction
