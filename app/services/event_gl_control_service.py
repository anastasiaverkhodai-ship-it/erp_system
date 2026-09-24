"""Read-only, per-event comparison of immutable economic events with GL.

A zero aggregate difference is insufficient: missing, duplicate, shifted or
misposted source journals remain explicit issues. Source dates and journal dates
both select the range, so moving a journal out of a period cannot hide it.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine

ZERO = Decimal("0")
MAX_EVENTS = 10000


@dataclass(frozen=True)
class EventControlSpec:
    model: type
    journal_field: str
    date_field: str
    amount_field: str
    debit_role: object
    credit_role: object


class EventGlIssue(BaseModel):
    source_type: str
    source_id: int
    journal_id: int | None = None
    code: str


class EventGlControl(BaseModel):
    source_type: str
    event_count: int
    expected_amount: Decimal
    posted_amount: Decimal
    difference: Decimal
    matched: bool
    issues: list[EventGlIssue]


def compare_event_rows(*, spec, rows, account_ids, focus_account_id, normal_credit,
                       date_from, date_to, planned_lines=None):
    groups = {}
    for event, entry, line, original in rows:
        group = groups.setdefault(event.id, {"event": event, "entries": {}})
        if entry is not None:
            journal = group["entries"].setdefault(entry.id, {
                "entry": entry, "lines": {}, "original": original})
            if line is not None:
                journal["lines"][line.id] = line
    if len(groups) > MAX_EVENTS:
        raise ValueError("Too many events; request a shorter control range")
    source_type = spec.model.__tablename__
    issues = []
    expected = posted = ZERO
    for group in groups.values():
        event = group["event"]
        journals = group["entries"]
        event_date = getattr(event, spec.date_field)
        amount = Decimal(getattr(event, spec.amount_field))
        valid = amount.is_finite() and amount >= ZERO
        def issue(code, entry=None):
            issues.append(EventGlIssue(source_type=source_type, source_id=event.id,
                journal_id=entry.id if entry is not None else None, code=code))
        if not valid:
            issue("invalid_source_amount")
        if getattr(event, "currency_code", "UAH") != "UAH":
            issue("unsupported_currency")
        wanted = defaultdict(lambda: [ZERO, ZERO])
        if planned_lines is None:
            debit_account = account_ids[spec.debit_role]
            credit_account = account_ids[spec.credit_role]
            wanted[debit_account][0] += amount if valid else ZERO
            wanted[credit_account][1] += amount if valid else ZERO
        else:
            for account_id, (debit, credit) in planned_lines.items():
                wanted[account_id] = [debit, credit]
        if event.reversal_of_id is not None:
            wanted = {account_id: [credit, debit] for account_id, (debit, credit) in wanted.items()}
        if valid and date_from <= event_date <= date_to:
            debit, credit = wanted.get(focus_account_id, (ZERO, ZERO))
            expected += credit - debit if normal_credit else debit - credit
        if valid and len(journals) != (1 if amount > ZERO else 0):
            issue("journal_count")
        for data in journals.values():
            entry = data["entry"]
            is_posted = entry.status in ("posted", "reversed")
            if not is_posted:
                issue("journal_not_posted", entry)
            if entry.entry_date != event_date:
                issue("journal_date_mismatch", entry)
            if event.reversal_of_id is None:
                if entry.reversal_of_id is not None:
                    issue("unexpected_reversal", entry)
            elif data["original"] is None or entry.reversal_of_id != data["original"].id:
                issue("reversal_link_mismatch", entry)
            actual = defaultdict(lambda: [ZERO, ZERO])
            finite = True
            for line in data["lines"].values():
                if not line.debit.is_finite() or not line.credit.is_finite():
                    finite = False
                    issue("invalid_journal_amount", entry)
                    continue
                actual[line.account_id][0] += line.debit
                actual[line.account_id][1] += line.credit
                if is_posted and line.account_id == focus_account_id and date_from <= entry.entry_date <= date_to:
                    posted += line.credit - line.debit if normal_credit else line.debit - line.credit
            if valid and (not finite or dict(actual) != dict(wanted)):
                issue("journal_accounts_or_amounts_mismatch", entry)
    return EventGlControl(source_type=source_type, event_count=len(groups),
        expected_amount=expected, posted_amount=posted, difference=posted - expected,
        matched=not issues and expected == posted, issues=issues)


async def reconcile_event_gl(db, *, spec, company_id, date_from, date_to,
                             account_ids, focus_account_id, normal_credit=False):
    if company_id <= 0 or date_to < date_from or (date_to - date_from).days > 366:
        raise ValueError("Provide a valid company and a range of at most 367 days")
    model = spec.model
    date_column = getattr(model, spec.date_field)
    link = getattr(JournalEntry, spec.journal_field)
    selected_journal = aliased(JournalEntry)
    original = aliased(JournalEntry)
    selected = select(model.id).where(model.company_id == company_id, or_(
        date_column.between(date_from, date_to),
        select(selected_journal.id).where(selected_journal.company_id == company_id,
            getattr(selected_journal, spec.journal_field) == model.id,
            selected_journal.entry_date.between(date_from, date_to)).exists()
    )).order_by(model.id).limit(MAX_EVENTS + 1)
    rows = (await db.execute(select(model, JournalEntry, JournalEntryLine, original)
        .outerjoin(JournalEntry, and_(JournalEntry.company_id == company_id, link == model.id))
        .outerjoin(JournalEntryLine, JournalEntryLine.journal_entry_id == JournalEntry.id)
        .outerjoin(original, and_(original.company_id == company_id,
            getattr(original, spec.journal_field) == model.reversal_of_id,
            original.reversal_of_id.is_(None)))
        .where(model.company_id == company_id, model.id.in_(selected))
        .order_by(model.id, JournalEntry.id, JournalEntryLine.id)
        .execution_options(populate_existing=True))).all()
    return compare_event_rows(spec=spec, rows=rows, account_ids=account_ids,
        focus_account_id=focus_account_id, normal_credit=normal_credit,
        date_from=date_from, date_to=date_to)
