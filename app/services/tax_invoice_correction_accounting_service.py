"""OUTPUT RK tax posting. Commercial return journals never substitute for VAT."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.tax_invoice_correction import TaxInvoiceCorrection
from app.models.tax_invoice_correction_line import TaxInvoiceCorrectionLine
from app.models.tax_invoice_correction_registration_event import TaxInvoiceCorrectionRegistrationEvent
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.services.accounting_account_roles import AccountingAccountRole as Role
from app.services.accounting_account_role_resolver import resolve_company_account_roles
from app.services.accounting_posting import post_journal_entry
from app.services.accounting_period_service import ensure_period_open


class CorrectionAccountingError(ValueError):
    pass


def registration_party(*, buyer_vat_number, compensation_delta):
    return 'buyer' if buyer_vat_number and compensation_delta < 0 else 'seller'


def correction_posting_date(*, document_date, compensation_delta, party, registered_on=None, received_on=None):
    """Ordinary domestic RK, current paragraph 89 registration regime."""
    if compensation_delta >= 0:
        return document_date
    if registered_on is None:
        return None
    if registered_on < document_date:
        raise CorrectionAccountingError('Registration cannot predate RK')
    if party == 'buyer':
        if received_on is None or not document_date <= received_on <= registered_on:
            raise CorrectionAccountingError('Buyer-registered decreasing RK requires its attested received_on date')
        deadline = received_on + timedelta(days=18)
    else:
        following = document_date.replace(day=monthrange(document_date.year,document_date.month)[1])+timedelta(days=1)
        deadline = following.replace(day=5 if document_date.day<=15 else 18)
    return document_date if registered_on <= deadline else registered_on


async def post_output_correction(db: AsyncSession, *, company_id, correction_id, created_by):
    correction=await db.scalar(select(TaxInvoiceCorrection).where(
        TaxInvoiceCorrection.company_id==company_id,TaxInvoiceCorrection.id==correction_id).with_for_update())
    if correction is None:
        raise CorrectionAccountingError('RK not found in company')
    if correction.direction!='output':
        return ()
    lines=list((await db.scalars(select(TaxInvoiceCorrectionLine).where(
        TaxInvoiceCorrectionLine.company_id==company_id,TaxInvoiceCorrectionLine.tax_invoice_correction_id==correction_id).order_by(TaxInvoiceCorrectionLine.line_number))).all())
    total=sum((line.total_with_vat_delta for line in lines),Decimal('0'))
    party=registration_party(buyer_vat_number=correction.buyer_vat_number,compensation_delta=total)
    registered=await db.scalar(select(TaxInvoiceCorrectionRegistrationEvent).where(
        TaxInvoiceCorrectionRegistrationEvent.company_id==company_id,
        TaxInvoiceCorrectionRegistrationEvent.tax_invoice_correction_id==correction_id,
        TaxInvoiceCorrectionRegistrationEvent.status=='registered').order_by(TaxInvoiceCorrectionRegistrationEvent.id.desc()).limit(1))
    effective=correction_posting_date(document_date=correction.document_date,compensation_delta=total,party=party,
        registered_on=registered.event_date if registered else None,received_on=registered.received_on if registered else None)
    if effective is None:
        return ()
    if effective>date.today():
        raise CorrectionAccountingError('Future RK tax posting is not supported')
    if total<0 and not correction.buyer_vat_number:
        raise CorrectionAccountingError('Non-VAT buyer deduction requires a separate full-return/refund evidence workflow')
    postings=[]
    for line in lines:
        # Recognition-reversal sources already have their own typed GL journal.
        if line.source_kind not in ('sales_return','sales_value_correction') or line.tax_amount_delta==0:
            continue
        from app.models.sales_return_recognition_event import SalesReturnRecognitionEvent
        from app.models.trade_value_correction_event import TradeValueCorrectionEvent
        model=SalesReturnRecognitionEvent if line.source_kind=='sales_return' else TradeValueCorrectionEvent
        source_id=line.sales_return_recognition_event_id if line.source_kind=='sales_return' else line.trade_value_correction_event_id
        source=await db.scalar(select(model).where(model.company_id==company_id,model.id==source_id))
        if source is None or source.currency_code!='UAH':
            raise CorrectionAccountingError('RK economic source is missing or unsupported')
        source_date=source.recognition_date if line.source_kind=='sales_return' else source.correction_date
        sign=-1 if source.reversal_of_id else 1
        if line.source_kind=='sales_return':
            expected_tax=-source.returned_tax_amount*sign
            expected_base=-(source.returned_gross_amount-source.returned_tax_amount)*sign
        else:
            expected_tax=(source.corrected_tax_amount-source.original_tax_amount)*sign
            expected_base=(source.corrected_gross_amount-source.original_gross_amount)*sign-expected_tax
        if source_date!=correction.document_date or (expected_base,expected_tax)!=(line.taxable_base_delta,line.tax_amount_delta):
            raise CorrectionAccountingError('RK date or amounts differ from its economic source')
        existing=await db.scalar(select(JournalEntry).where(JournalEntry.company_id==company_id,
            JournalEntry.tax_invoice_correction_line_id==line.id))
        if existing:
            if existing.entry_date!=effective or existing.status not in ('posted','reversed'):
                raise CorrectionAccountingError('Existing RK posting is inconsistent')
            postings.append(existing)
            continue
        await ensure_period_open(db=db,company_id=company_id,operation_date=effective)
        contra=Role.SALES_DEDUCTIONS if line.source_kind=='sales_return' else Role.GOODS_REVENUE
        accounts=await resolve_company_account_roles(db,company_id=company_id,roles=(Role.TAX_SETTLEMENT,contra))
        amount=abs(line.tax_amount_delta)
        debit,credit=(contra,Role.TAX_SETTLEMENT) if line.tax_amount_delta>0 else (Role.TAX_SETTLEMENT,contra)
        entry=JournalEntry(company_id=company_id,tax_invoice_correction_line_id=line.id,
            entry_date=effective,status='draft',description=f'OUTPUT VAT RK {correction.document_number}, line {line.line_number}',created_by=created_by)
        entry.lines=[JournalEntryLine(line_no=1,account_id=accounts[debit].id,debit=amount,credit=Decimal('0')),
                     JournalEntryLine(line_no=2,account_id=accounts[credit].id,debit=Decimal('0'),credit=amount)]
        db.add(entry)
        await db.flush()
        await post_journal_entry(db,company_id,entry.id)
        postings.append(entry)
    return tuple(postings)


async def load_posted_output_corrections(db, *, company_id, period_start, period_end, source_cutoff_at):
    """Declaration inputs follow tax posting dates, not unregistered returns."""
    from datetime import timezone
    cutoff=source_cutoff_at.astimezone(timezone.utc).replace(tzinfo=None)
    records=(await db.execute(select(TaxInvoiceCorrectionLine,JournalEntry).join(JournalEntry,
        (JournalEntry.company_id==TaxInvoiceCorrectionLine.company_id)&
        (JournalEntry.tax_invoice_correction_line_id==TaxInvoiceCorrectionLine.id))
        .join(TaxInvoiceCorrection,(TaxInvoiceCorrection.company_id==TaxInvoiceCorrectionLine.company_id)&
              (TaxInvoiceCorrection.id==TaxInvoiceCorrectionLine.tax_invoice_correction_id))
        .where(TaxInvoiceCorrection.company_id==company_id,TaxInvoiceCorrection.direction=='output',
            JournalEntry.entry_date>=period_start,JournalEntry.entry_date<=period_end,
            JournalEntry.created_at<=cutoff,JournalEntry.posted_at<=cutoff,
            TaxInvoiceCorrection.created_at<=source_cutoff_at,JournalEntry.status.in_(('posted','reversed')))
        .order_by(JournalEntry.entry_date,JournalEntry.id))).all()
    result=[]
    for line,journal in records:
        if line.source_kind not in ('sales_return','sales_value_correction'):
            raise CorrectionAccountingError('Unexpected source for RK tax journal')
        result.append(dict(source_kind=line.source_kind,
            source_id=line.sales_return_recognition_event_id if line.source_kind=='sales_return' else line.trade_value_correction_event_id,
            economic_effective_date=journal.entry_date,direction='output',taxable_base_delta=line.taxable_base_delta,
            tax_amount_delta=line.tax_amount_delta,currency_code='UAH',tax_rate_code=line.tax_rate_code,tax_rate=line.tax_rate))
    return result
