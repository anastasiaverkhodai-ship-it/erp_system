"""Read-only reconciliation of OUTPUT recognition events to their GL entries.

This is a source-level control, not the full 641 balance or a VAT declaration.
Reversed original journals remain in turnover together with their reversal.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.tax_calculation import TaxCalculation
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.services.accounting_account_roles import AccountingAccountRole as Role
from app.services.accounting_account_role_resolver import resolve_company_account_roles
from app.services.tax_recognition_journal_service import (
    resolve_output_vat_recognition_source_kind, TaxRecognitionJournalError,
    validate_input_vat_recognition_source,
)
from app.services.tax_recognition_accounting_service import OutputVatRecognitionSourceKind

ZERO = Decimal('0.00')
MAX_EVENTS = 10000


class OutputVatGlReconciliationError(ValueError):
    pass


class OutputVatGlIssue(BaseModel):
    event_id: int
    calculation_id: int
    document_id: int
    journal_id: int | None = None
    code: str


class OutputVatGlReconciliation(BaseModel):
    company_id: int
    date_from: date
    date_to: date
    event_count: int
    expected_output_vat: Decimal
    posted_output_vat: Decimal
    difference: Decimal
    matched: bool
    issues: list[OutputVatGlIssue]


def reconcile_output_vat_rows(*, company_id, date_from, date_to, rows, account_ids, direction="output"):
    """Compare each source before totals, so offsetting errors cannot disappear."""
    if direction not in ("input", "output"):
        raise ValueError("Invalid VAT direction")
    grouped = {}
    for event, calculation, entry, line, original_entry in rows:
        group = grouped.setdefault(event.id, dict(event=event, calculation=calculation, entries={}))
        if entry is not None:
            item = group['entries'].setdefault(entry.id, dict(entry=entry, lines={}, original=original_entry))
            if line is not None:
                item['lines'][line.id] = line
    if len(grouped) > MAX_EVENTS:
        raise OutputVatGlReconciliationError('Too many VAT events; request a shorter date range')
    expected = posted = ZERO
    issues = []
    tax_account = account_ids[Role.TAX_SETTLEMENT]
    for group in grouped.values():
        event, calc, entries = group['event'], group['calculation'], group['entries']
        def issue(code, entry=None):
            issues.append(OutputVatGlIssue(event_id=event.id, calculation_id=calc.id,
                document_id=calc.trade_document_id, journal_id=entry.id if entry else None, code=code))
        amount = Decimal(event.recognized_tax_amount)
        valid_amount = amount.is_finite() and amount >= ZERO
        if not valid_amount:
            issue('invalid_event_amount')
        elif date_from <= event.recognition_date <= date_to:
            expected += -amount if event.reversal_of_id else amount
        if event.currency_code != 'UAH' or calc.currency_code != 'UAH':
            issue('unsupported_currency')
        try:
            if direction == "input":
                validate_input_vat_recognition_source(event)
                kind = "input"
            else:
                kind = resolve_output_vat_recognition_source_kind(event)
        except TaxRecognitionJournalError:
            kind = None
            issue('invalid_event_source')
        if valid_amount:
            if amount == ZERO and entries:
                issue('unexpected_zero_tax_journal')
            elif amount > ZERO and not entries:
                issue('missing_journal')
            elif amount > ZERO and len(entries) != 1:
                issue('journal_count')
        for item in entries.values():
            entry, lines = item['entry'], list(item['lines'].values())
            is_posted = entry.status in ('posted', 'reversed')
            if not is_posted:
                issue('journal_not_posted', entry)
            if entry.entry_date != event.recognition_date:
                issue('journal_date_mismatch', entry)
            if event.reversal_of_id is None:
                if entry.reversal_of_id is not None:
                    issue('unexpected_journal_reversal', entry)
            elif (item['original'] is None or entry.reversal_of_id != item['original'].id):
                issue('reversal_link_mismatch', entry)
            actual = defaultdict(lambda: [ZERO, ZERO])
            finite = True
            for line in lines:
                if not line.debit.is_finite() or not line.credit.is_finite():
                    finite = False
                    issue('invalid_journal_amount', entry)
                    continue
                actual[line.account_id][0] += line.debit
                actual[line.account_id][1] += line.credit
                if is_posted and line.account_id == tax_account and date_from <= entry.entry_date <= date_to:
                    posted += (line.debit - line.credit) if direction == "input" else (line.credit - line.debit)
            if valid_amount and kind is not None:
                if direction == "input":
                    wanted = {tax_account: [amount, ZERO], account_ids[Role.VAT_INPUT]: [ZERO, amount]}
                else:
                    source_role = Role.GOODS_REVENUE if kind == OutputVatRecognitionSourceKind.FULFILLMENT else Role.VAT_OUTPUT
                    debit_account = account_ids[source_role]
                    wanted = {debit_account: [amount, ZERO], tax_account: [ZERO, amount]}
                if event.reversal_of_id:
                    wanted = {account: [credit, debit] for account, (debit, credit) in wanted.items()}
                if not finite or dict(actual) != wanted:
                    issue('journal_accounts_or_amounts_mismatch', entry)
    difference = posted - expected
    return OutputVatGlReconciliation(company_id=company_id, date_from=date_from, date_to=date_to,
        event_count=len(grouped), expected_output_vat=expected, posted_output_vat=posted,
        difference=difference, matched=not issues and difference == ZERO, issues=issues)


async def reconcile_output_vat_gl(db, *, company_id: int, date_from: date, date_to: date):
    return await _reconcile_vat_gl(db, company_id=company_id, date_from=date_from, date_to=date_to, direction="output")


async def _reconcile_vat_gl(db, *, company_id: int, date_from: date, date_to: date, direction: str):
    if company_id <= 0 or date_to < date_from or (date_to - date_from).days > 366:
        raise OutputVatGlReconciliationError('Provide a valid company and a date range of at most 367 days')
    accounts = await resolve_company_account_roles(db, company_id=company_id,
        roles=((Role.TAX_SETTLEMENT, Role.VAT_INPUT) if direction == "input"
               else (Role.TAX_SETTLEMENT, Role.VAT_OUTPUT, Role.GOODS_REVENUE)))
    ids = {role: account.id for role, account in accounts.items()}
    if len(set(ids.values())) != len(ids):
        raise OutputVatGlReconciliationError('VAT control requires distinct accounting role accounts')
    entry_in_range = aliased(JournalEntry)
    original = aliased(JournalEntry)
    # Select event IDs first, then all their journals even if a wrong date falls
    # outside the requested period. One data SELECT provides a consistent snapshot.
    selected = select(TaxRecognitionEvent.id).join(TaxCalculation, and_(
        TaxCalculation.company_id == TaxRecognitionEvent.company_id,
        TaxCalculation.id == TaxRecognitionEvent.tax_calculation_id)).where(
        TaxRecognitionEvent.company_id == company_id, TaxCalculation.direction == direction,
        or_(TaxRecognitionEvent.recognition_date.between(date_from, date_to),
            select(entry_in_range.id).where(entry_in_range.company_id == company_id,
                entry_in_range.tax_recognition_event_id == TaxRecognitionEvent.id,
                entry_in_range.entry_date.between(date_from, date_to)).exists())
    ).order_by(TaxRecognitionEvent.id).limit(MAX_EVENTS + 1)
    rows = (await db.execute(select(TaxRecognitionEvent, TaxCalculation, JournalEntry, JournalEntryLine, original)
        .join(TaxCalculation, and_(TaxCalculation.company_id == TaxRecognitionEvent.company_id,
            TaxCalculation.id == TaxRecognitionEvent.tax_calculation_id))
        .outerjoin(JournalEntry, and_(JournalEntry.company_id == company_id,
            JournalEntry.tax_recognition_event_id == TaxRecognitionEvent.id))
        .outerjoin(JournalEntryLine, JournalEntryLine.journal_entry_id == JournalEntry.id)
        .outerjoin(original, and_(original.company_id == company_id,
            original.tax_recognition_event_id == TaxRecognitionEvent.reversal_of_id, original.reversal_of_id.is_(None)))
        .where(TaxRecognitionEvent.company_id == company_id, TaxRecognitionEvent.id.in_(selected))
        .order_by(TaxRecognitionEvent.id, JournalEntry.id, JournalEntryLine.id)
        .execution_options(populate_existing=True))).all()
    return reconcile_output_vat_rows(company_id=company_id, date_from=date_from, date_to=date_to,
        rows=rows, account_ids=ids, direction=direction)
