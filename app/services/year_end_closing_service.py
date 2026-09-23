"""Explicit, repeatable year-end journals; callers own transactions."""
import calendar
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.company import Company
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.models.year_end_closing import YearEndClosing
from app.schemas.year_end_closing import YearEndPreview, YearEndPreviewLine, YearEndPreviewRequest
from app.services.accounting_posting import post_journal_entry
from app.services.accounting_reversal import reverse_journal_entry
from app.services.idempotency_fingerprint_service import generate_request_fingerprint

ZERO = Decimal("0")


class YearEndError(Exception):
    pass


class YearEndNotFound(YearEndError):
    pass


def _year_end(year):
    if not 2000 <= year <= 2100:
        raise YearEndError("Year must be between 2000 and 2100")
    return date(year, 12, 31)


@contextmanager
def _journal_lifecycle(db, closing_id):
    previous = db.info.get("year_end_closing_active")
    db.info["year_end_closing_active"] = closing_id
    try:
        yield
    finally:
        if previous is None:
            db.info.pop("year_end_closing_active", None)
        else:
            db.info["year_end_closing_active"] = previous


async def ensure_year_not_closed(db, company_id, year):
    """Caller holds the affected period lock, preventing close/reopen races."""
    closed = await db.scalar(select(YearEndClosing.id).where(
        YearEndClosing.company_id == company_id,
        YearEndClosing.year >= year,
        YearEndClosing.status == "closed",
    ).limit(1))
    if closed is not None and db.info.get("year_end_closing_active") != closed:
        raise HTTPException(409, "Year is closed; reverse its year-end closing first")


async def _company(db, company_id, lock=False):
    query = select(Company).where(Company.id == company_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    company = await db.scalar(query)
    if company is None:
        raise YearEndNotFound("Company not found")
    if not company.is_active:
        raise YearEndError("Company is inactive")
    return company


async def _balances(db, company_id, end):
    rows = (await db.execute(select(
        JournalEntryLine.account_id,
        func.sum(JournalEntryLine.debit - JournalEntryLine.credit),
    ).join(JournalEntry, JournalEntry.id == JournalEntryLine.journal_entry_id).where(
        JournalEntry.company_id == company_id,
        JournalEntry.entry_date <= end,
        JournalEntry.status.in_([JournalEntryStatus.POSTED, JournalEntryStatus.REVERSED]),
    ).group_by(JournalEntryLine.account_id))).all()
    return {account_id: balance for account_id, balance in rows if balance != ZERO}


async def preview_year_end(db: AsyncSession, company_id: int, year: int, data: YearEndPreviewRequest):
    end = _year_end(year)
    company = await _company(db, company_id)
    accounts = {a.id: a for a in (await db.scalars(select(Account).where(
        Account.company_id == company_id))).all()}
    def account(aid, prefix=None):
        a = accounts.get(aid)
        if a is None or not a.is_active or not a.is_postable:
            raise YearEndError(f"Invalid, inactive, non-postable or foreign account: {aid}")
        if prefix and (not a.code.startswith(prefix) or a.account_type != "equity"):
            raise YearEndError(f"Account {aid} must be a {prefix} equity account")
        return a
    profit_account = account(data.profit_account_id, "44")
    loss_account = account(data.loss_account_id, "44")
    # The caller explicitly selects working accounts; never create/remap accounts silently.
    general = company.chart_of_accounts_template == "general_291"
    if general:
        if profit_account.code not in ("44", "441") and not profit_account.code.startswith("441"):
            raise YearEndError("Profit destination must be 44/441")
        if loss_account.code not in ("44", "442") and not loss_account.code.startswith("442"):
            raise YearEndError("Loss destination must be 44/442")
    mapping = {m.source_account_id: m.result_account_id for m in data.mappings}
    for source_id, target_id in mapping.items():
        source, target = account(source_id), account(target_id, "79")
        if source.account_type not in ("income", "expense"):
            raise YearEndError("Closing mappings accept only income/expense accounts")
        if general:
            expected = {"70":"791", "71":"791", "90":"791", "92":"791", "93":"791", "94":"791", "98":"791",
                        "72":"792", "73":"792", "95":"792", "96":"792", "74":"793", "97":"793"}.get(source.code[:2])
            if expected is None:
                raise YearEndError(f"Account {source.code} requires allocation before year-end closing")
            if target.code != "79" and not target.code.startswith(expected):
                raise YearEndError(f"Account {source.code} must close through {expected} or aggregate 79")
    balances = await _balances(db, company_id, end)
    prior = await _balances(db, company_id, date(year-1, 12, 31))
    for aid, amount in prior.items():
        a = accounts.get(aid)
        if a is None:
            raise YearEndError("Journal contains a foreign or missing account")
        if a.account_type in ("income", "expense") or a.code.startswith("79"):
            raise YearEndError("Unclosed income/expense/result balance from a prior year")
    if sum(balances.values(), ZERO) != ZERO:
        raise YearEndError("General ledger is not balanced")
    lines = []
    result_balances = {}
    def line(aid, raw, description):
        if raw:
            lines.append(YearEndPreviewLine(account_id=aid, debit=max(raw,ZERO), credit=max(-raw,ZERO), description=description))
    for aid, balance in sorted(balances.items()):
        a = accounts.get(aid)
        if a is None:
            raise YearEndError("Journal contains a foreign or missing account")
        if a.code.startswith("79"):
            account(aid, "79")
            result_balances[aid] = result_balances.get(aid,ZERO) + balance
        elif a.account_type in ("income", "expense"):
            account(aid)
            if aid not in mapping:
                raise YearEndError(f"Missing closing mapping for account {a.code} ({aid})")
            target = mapping[aid]
            line(aid, -balance, "Close income/expense")
            line(target, balance, "Transfer to financial result")
            result_balances[target] = result_balances.get(target,ZERO) + balance
    profit = -sum(result_balances.values(),ZERO)
    destination = data.profit_account_id if profit >= ZERO else data.loss_account_id
    for aid, balance in sorted(result_balances.items()):
        line(aid, -balance, "Close financial result")
        line(destination, balance, "Retained profit / uncovered loss")
    assert sum((l.debit-l.credit for l in lines),ZERO) == ZERO
    config = data.model_dump(mode="json", include={"profit_account_id","loss_account_id","mappings"})
    fingerprint = generate_request_fingerprint({"company_id":company_id,"year":year,"config":config,
        "balances":{str(k):str(v) for k,v in sorted(balances.items())},
        "lines":[l.model_dump(mode="json") for l in lines]})
    return YearEndPreview(company_id=company_id,year=year,profit=profit,lines=lines,preview_fingerprint=fingerprint)


async def _lock_periods(db, company_id, year):
    periods = (await db.scalars(select(AccountingPeriod).where(
        AccountingPeriod.company_id == company_id, AccountingPeriod.year <= year,
    ).order_by(AccountingPeriod.year,AccountingPeriod.month).with_for_update()
        .execution_options(populate_existing=True))).all()
    current = [p for p in periods if p.year == year]
    if {p.month for p in current} != set(range(1,13)):
        raise YearEndError("All twelve monthly accounting periods are required")
    for p in current:
        if p.start_date != date(year,p.month,1) or p.end_date != date(year,p.month,calendar.monthrange(year,p.month)[1]):
            raise YearEndError("Accounting periods must have canonical calendar boundaries")
    for p in periods:
        if p.year < year or p.month < 12:
            if p.status != "closed" or not p.is_locked:
                raise YearEndError("All earlier periods must be closed before closing the year")
    return current[-1]


async def close_year(db, company_id, year, actor_id, data):
    end = _year_end(year)
    if actor_id <= 0 or end >= date.today():
        raise YearEndError("Only completed calendar years can be closed")
    await _company(db,company_id,lock=True)
    request_fingerprint = generate_request_fingerprint({"year":year,"data":data.model_dump(mode="json")})
    existing = await db.scalar(select(YearEndClosing).where(
        YearEndClosing.company_id == company_id, YearEndClosing.request_key == data.request_key))
    if existing is not None:
        if existing.request_fingerprint != request_fingerprint:
            raise YearEndError("Request key already used for different closing data")
        return existing
    await ensure_year_not_closed(db, company_id, year)
    december = await _lock_periods(db,company_id,year)
    if december.status != "open" or december.is_locked:
        raise YearEndError("December must be open for the closing journal")
    draft = await db.scalar(select(JournalEntry.id).where(JournalEntry.company_id == company_id,
        JournalEntry.entry_date <= end, JournalEntry.status == JournalEntryStatus.DRAFT).limit(1))
    if draft is not None:
        raise YearEndError("Unresolved draft journals exist on or before year-end")
    preview = await preview_year_end(db,company_id,year,data)
    if preview.preview_fingerprint != data.preview_fingerprint:
        raise YearEndError("Preview is stale; review a fresh preview before closing")
    closing = YearEndClosing(company_id=company_id,year=year,created_by=actor_id,
        request_key=data.request_key,request_fingerprint=request_fingerprint,
        preview_fingerprint=preview.preview_fingerprint,status="closed")
    db.add(closing)
    await db.flush()
    if preview.lines:
        journal = JournalEntry(company_id=company_id,entry_date=end,created_by=actor_id,
            year_end_closing_id=closing.id,status=JournalEntryStatus.DRAFT,
            description=f"Year-end closing {year}")
        journal.lines = [JournalEntryLine(line_no=i,**line.model_dump()) for i,line in enumerate(preview.lines,1)]
        db.add(journal)
        await db.flush()
        closing.journal_entry_id = journal.id
        with _journal_lifecycle(db,closing.id):
            await post_journal_entry(db,company_id,journal.id)
    december.status = "closed"
    december.is_locked = True
    december.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()
    return closing


async def get_closing(db, company_id, closing_id):
    result = await db.scalar(select(YearEndClosing).where(
        YearEndClosing.company_id == company_id,YearEndClosing.id == closing_id))
    if result is None:
        raise YearEndNotFound("Year-end closing not found")
    return result


async def reverse_year_end(db,company_id,closing_id,actor_id):
    if actor_id <= 0:
        raise YearEndError("Invalid actor")
    await _company(db,company_id,lock=True)
    closing = await db.scalar(select(YearEndClosing).where(
        YearEndClosing.company_id == company_id,YearEndClosing.id == closing_id)
        .with_for_update().execution_options(populate_existing=True))
    if closing is None:
        raise YearEndNotFound("Year-end closing not found")
    if closing.status == "reversed":
        return closing
    later = await db.scalar(select(YearEndClosing.id).where(
        YearEndClosing.company_id == company_id,YearEndClosing.year > closing.year,
        YearEndClosing.status == "closed").limit(1))
    if later is not None:
        raise YearEndError("Reverse later year-end closings first")
    december = await _lock_periods(db,company_id,closing.year)
    december.status = "open"
    december.is_locked = False
    december.closed_at = None
    await db.flush()
    if closing.journal_entry_id is not None:
        with _journal_lifecycle(db,closing.id):
            await reverse_journal_entry(db,company_id,closing.journal_entry_id,
                date(closing.year,12,31),actor_id)
    closing.status = "reversed"
    closing.reversed_by = actor_id
    closing.reversed_at = datetime.now(timezone.utc)
    await db.flush()
    return closing
