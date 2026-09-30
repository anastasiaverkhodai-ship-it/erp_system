"""FA subledger reconstructed from immutable lifecycle events, never current cost.

Reversed originals remain effective until the reversal's own accounting date.
Missing journals do not erase expected subledger movements. GL comparison includes
both gross turnovers and balances, so equal/opposite mistakes cannot net away.
"""
from collections import defaultdict
from datetime import date, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.company import Company
from app.models.fixed_asset import FixedAsset
from app.models.fixed_asset_acquisition import FixedAssetAcquisitionCost
from app.models.fixed_asset_commissioning import FixedAssetCommissioning
from app.models.fixed_asset_depreciation import FixedAssetDepreciation
from app.models.fixed_asset_disposal import FixedAssetDisposal
from app.models.fixed_asset_opening_balance import FixedAssetOpeningBalance
from app.models.fixed_asset_repair_improvement import FixedAssetRepairImprovement
from app.models.fixed_asset_revaluation_impairment import FixedAssetRevaluationImpairment
from app.models.opening_balance import OpeningBalance
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.schemas.fixed_asset_report import (
    FixedAssetReportIssue, FixedAssetReportEvent, FixedAssetMovementLine,
    FixedAssetMovementTotals, FixedAssetMovementReport, FixedAssetGLBalance,
    FixedAssetGLLine, FixedAssetGLReconciliation, FixedAssetDepreciationReport,
)

ZERO = Decimal('0.00')
POSTED = (JournalEntryStatus.POSTED, JournalEntryStatus.REVERSED)
SOURCES = (
    ('commissioning', FixedAssetCommissioning, 'commissioning_date', 'fixed_asset_commissioning_id'),
    ('depreciation', FixedAssetDepreciation, 'posting_date', 'fixed_asset_depreciation_id'),
    ('improvement', FixedAssetRepairImprovement, 'operation_date', 'fixed_asset_repair_improvement_id'),
    ('valuation', FixedAssetRevaluationImpairment, 'operation_date', 'fixed_asset_revaluation_impairment_id'),
    ('disposal', FixedAssetDisposal, 'disposal_date', 'fixed_asset_disposal_id'),
)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _vector():
    return defaultdict(lambda: [ZERO, ZERO])


def _add(vector, account_id, debit=ZERO, credit=ZERO):
    vector[account_id][0] += Decimal(debit)
    vector[account_id][1] += Decimal(credit)


def _clean(vector):
    return {k: tuple(v) for k, v in vector.items() if any(v)}


def _turnover(target, when, start, debit, credit):
    if when < start:
        target.opening += debit - credit
    else:
        target.period_debit += debit
        target.period_credit += credit
    target.closing += debit - credit


async def _build(db: AsyncSession, company_id: int, date_from: date, date_to: date):
    if date_from > date_to:
        raise ValueError('date_from must not be after date_to')
    # HTTP callers provide a repeatable-read, read-only transaction. Service
    # callers own their transaction and may also report on uncommitted events.
    company = await db.scalar(select(Company.id).where(Company.id == company_id))
    if company is None:
        raise ValueError('company not found')
    assets = {a.id: a for a in (await db.scalars(select(FixedAsset).where(FixedAsset.company_id == company_id))).all()}
    accounts = {a.id: a for a in (await db.scalars(select(Account).where(Account.company_id == company_id))).all()}
    controlled = {i for a in assets.values() for i in (a.asset_account_id, a.accumulated_depreciation_account_id)}
    # Also cover standard FA accounts without an assigned card: manual postings
    # to an orphan 10/11/12/13 account must not disappear from reconciliation.
    controlled.update(a.id for a in accounts.values() if a.code.startswith(('10', '11', '12', '13')))
    issues = []
    events = []
    expected = defaultdict(FixedAssetGLBalance)
    actual = defaultdict(FixedAssetGLBalance)
    journal_vectors = defaultdict(_vector)
    journals = {j.id: j for j in (await db.scalars(select(JournalEntry).where(JournalEntry.company_id == company_id))).all()}
    for line in (await db.scalars(select(JournalEntryLine).join(JournalEntry, JournalEntry.id == JournalEntryLine.journal_entry_id)
                                 .where(JournalEntry.company_id == company_id))).all():
        _add(journal_vectors[line.journal_entry_id], line.account_id, line.debit, line.credit)
    for jid, vector in journal_vectors.items():
        j = journals[jid]
        if j.status in POSTED and j.entry_date <= date_to:
            for aid, (debit, credit) in vector.items():
                if aid in controlled:
                    _turnover(actual[aid], j.entry_date, date_from, debit, credit)
                if aid not in accounts:
                    issues.append(FixedAssetReportIssue(code='foreign_account', message='Journal uses an account outside the company', journal_entry_id=jid))

    def issue(code, message, event=None, **kwargs):
        if event is not None:
            kwargs.update(fixed_asset_id=event.fixed_asset_id, event_type=event.event_type, event_id=event.event_id)
        issues.append(FixedAssetReportIssue(code=code, message=message, **kwargs))

    checked_journals = set()
    def record(event, vector, linked, *, required=True, compare=True):
        events.append(event)
        event.journal_entry_ids = sorted(j.id for j in linked)
        for aid, (debit, credit) in vector.items():
            if aid in controlled:
                _turnover(expected[aid], event.event_date, date_from, debit, credit)
        if not compare:
            return
        if len(linked) != (1 if required else 0):
            issue('journal_count', 'Expected exactly one lifecycle journal' if required else 'This event must not create a journal', event)
        for j in linked:
            checked_journals.add(j.id)
            if j.status not in POSTED:
                issue('journal_not_posted', 'Lifecycle journal is not posted', event, journal_entry_id=j.id)
            if j.entry_date != event.event_date:
                issue('journal_date', 'Journal date differs from lifecycle date', event, journal_entry_id=j.id)
            if _clean(journal_vectors[j.id]) != _clean(vector):
                issue('journal_amounts', 'Journal accounts or debit/credit amounts differ from lifecycle evidence', event, journal_entry_id=j.id)

    costs = {c.id: c for c in (await db.scalars(select(FixedAssetAcquisitionCost).where(FixedAssetAcquisitionCost.company_id == company_id))).all()}
    source_lines = {line.id: line for line in (await db.scalars(select(JournalEntryLine).join(JournalEntry,
        JournalEntry.id == JournalEntryLine.journal_entry_id).where(JournalEntry.company_id == company_id))).all()}
    source_rows = {}
    for kind, model, date_field, fk in SOURCES:
        source_rows[kind] = list((await db.scalars(select(model).where(model.company_id == company_id))).all())
    commissionings = {c.id: c for c in source_rows['commissioning']}

    def commissioning_costs(row, event):
        original = commissionings.get(row.reversal_of_id) if row.reversal_of_id else row
        if original is None:
            issue('missing_original', 'Commissioning reversal has no original event', event)
            return []
        if original.cost_snapshot is not None:
            snapshot = original.cost_snapshot
            for item in snapshot:
                cost = costs.get(item['cost_id'])
                line = source_lines.get(cost.source_journal_entry_line_id) if cost else None
                if (cost is None or cost.fixed_asset_id != row.fixed_asset_id or cost.reversal_of_id is not None
                    or Decimal(item['amount']) != cost.amount or line is None or line.account_id != item['account_id']):
                    issue('commissioning_source', 'Commissioning snapshot differs from its acquisition evidence', event)
            return snapshot
        # Legacy events have no snapshot. Recover only from allocation history
        # that unambiguously predates the original creation timestamp.
        cutoff = _utc(original.created_at)
        candidates = [c for c in costs.values() if c.fixed_asset_id == row.fixed_asset_id]
        reversals = {c.reversal_of_id: c for c in candidates if c.reversal_of_id}
        result = []
        for c in candidates:
            if c.reversal_of_id or _utc(c.created_at) > cutoff:
                continue
            rev = reversals.get(c.id)
            if rev and _utc(rev.created_at) == cutoff:
                issue('ambiguous_legacy_commissioning', 'Same-transaction allocation reversal requires historical source review', event)
                continue
            if rev and _utc(rev.created_at) < cutoff:
                continue
            line = source_lines.get(c.source_journal_entry_line_id)
            if line is not None:
                result.append(dict(cost_id=c.id, account_id=line.account_id, amount=str(c.amount)))
            else:
                issue('missing_source', 'Acquisition source line is missing', event)
        if not result:
            issue('missing_commissioning_basis', 'No independently verifiable commissioning cost basis', event)
        return result

    for kind, _, date_field, fk in SOURCES:
        for row in source_rows[kind]:
            when = getattr(row, date_field)
            if when > date_to:
                continue
            a = assets.get(row.fixed_asset_id)
            if a is None:
                issue('foreign_asset', 'Lifecycle event has no asset in this company', event_type=kind, event_id=row.id)
                continue
            sign = -1 if row.reversal_of_id else 1
            event = FixedAssetReportEvent(fixed_asset_id=a.id, event_type=kind, event_id=row.id,
                event_date=when, reversal_of_id=row.reversal_of_id)
            vector = _vector()
            required = True
            if kind == 'commissioning':
                basis = commissioning_costs(row, event)
                event.cost_change = sign * sum((Decimal(c['amount']) for c in basis), ZERO)
                _add(vector, a.asset_account_id, debit=abs(event.cost_change))
                for c in basis:
                    _add(vector, c['account_id'], credit=Decimal(c['amount']))
            elif kind == 'depreciation':
                event.amount = sign * row.amount
                event.accumulated_change = event.amount
                event.period_start, event.period_end = row.period_start, row.period_end
                event.method, event.actual_output = row.method, row.actual_output
                required = row.amount != 0
                _add(vector, a.depreciation_expense_account_id, debit=row.amount)
                _add(vector, a.accumulated_depreciation_account_id, credit=row.amount)
            elif kind == 'improvement':
                event.event_type = row.operation_type.value
                event.amount = sign * row.amount
                required = event.event_type == 'improvement'
                if required:
                    event.cost_change = event.amount
                    _add(vector, a.asset_account_id, debit=row.amount)
                    line = source_lines.get(row.source_journal_entry_line_id)
                    if line is None:
                        issue('missing_source', 'Improvement source line is missing', event)
                    else:
                        _add(vector, line.account_id, credit=row.amount)
            elif kind == 'valuation':
                event.event_type = row.operation_type.value
                event.cost_change = row.original_cost_after - row.original_cost_before
                event.amount = event.cost_change
                delta = event.cost_change * sign  # reversal snapshot already swaps before/after
                if delta > 0:
                    _add(vector, a.asset_account_id, debit=delta)
                    _add(vector, row.counterpart_account_id, credit=delta)
                else:
                    _add(vector, row.counterpart_account_id, debit=-delta)
                    _add(vector, a.asset_account_id, credit=-delta)
            elif kind == 'disposal':
                event.cost_change = -sign * row.original_cost
                event.accumulated_change = -sign * row.accumulated_depreciation
                event.amount = sign * row.carrying_amount
                event.sale_net_amount = sign * row.sale_net_amount if row.sale_net_amount is not None else None
                _add(vector, a.asset_account_id, credit=row.original_cost)
                _add(vector, a.accumulated_depreciation_account_id, debit=row.accumulated_depreciation)
                _add(vector, row.disposal_account_id, debit=row.carrying_amount)
            if sign < 0:
                vector = {aid: [credit, debit] for aid, (debit, credit) in vector.items()}
            # Some older improvement reversals only carry the direct event->JE link.
            direct_id = getattr(row, 'journal_entry_id', None)
            linked = [j for j in journals.values() if getattr(j, fk) == row.id or j.id == direct_id]
            record(event, vector, linked, required=required)

    openings = {o.id: o for o in (await db.scalars(select(OpeningBalance).where(OpeningBalance.company_id == company_id))).all()}
    opening_vectors = defaultdict(_vector)
    opening_dates = {}
    for detail in (await db.scalars(select(FixedAssetOpeningBalance).where(FixedAssetOpeningBalance.company_id == company_id))).all():
        a, opening = assets.get(detail.fixed_asset_id), openings.get(detail.opening_balance_id)
        if a is None or opening is None:
            issue('opening_source', 'Opening detail has no company-owned asset or source')
            continue
        # Detail creation requires a posted opening; retain expected amounts if
        # the journal was subsequently damaged or removed.
        occurrences = [(opening.journal_entry_id, opening.opening_date, 1, None)]
        occurrences += [(j.id, j.entry_date, -1, detail.id) for j in journals.values() if j.reversal_of_id == opening.journal_entry_id]
        for jid, when, sign, reversal in occurrences:
            if when > date_to:
                continue
            event = FixedAssetReportEvent(fixed_asset_id=a.id, event_type='opening', event_id=detail.id,
                event_date=when, reversal_of_id=reversal, cost_change=sign*detail.original_cost,
                accumulated_change=sign*detail.accumulated_depreciation)
            vector = _vector()
            _add(vector, a.asset_account_id, debit=detail.original_cost if sign > 0 else ZERO, credit=detail.original_cost if sign < 0 else ZERO)
            _add(vector, a.accumulated_depreciation_account_id,
                 credit=detail.accumulated_depreciation if sign > 0 else ZERO, debit=detail.accumulated_depreciation if sign < 0 else ZERO)
            record(event, vector, [journals[jid]] if jid in journals else [], compare=False)
            for aid, (debit, credit) in vector.items():
                _add(opening_vectors[jid], aid, debit, credit)
            opening_dates[jid] = when
    for jid, vector in opening_vectors.items():
        j = journals.get(jid)
        checked_journals.add(jid)
        if j is None or j.status not in POSTED:
            issue('opening_not_posted', 'Opening journal is missing or not posted', journal_entry_id=jid)
        if j and j.entry_date != opening_dates[jid]:
            issue('journal_date', 'Opening date differs from journal date', journal_entry_id=jid)
        actual_vector = {aid: values for aid, values in journal_vectors[jid].items() if aid in controlled}
        if _clean(actual_vector) != _clean(vector):
            issue('opening_amounts', 'FA opening details differ from journal amounts', journal_entry_id=jid)

    for j in journals.values():
        if j.status not in POSTED or j.entry_date > date_to or j.id in checked_journals:
            continue
        if any(aid in controlled and any(values) for aid, values in journal_vectors[j.id].items()) or any(getattr(j, fk) is not None for _, _, _, fk in SOURCES):
            issue('unexplained_journal', 'Posted FA journal has no matching lifecycle evidence', journal_entry_id=j.id)
    gl_lines = []
    for aid in sorted(controlled, key=lambda i: accounts[i].code if i in accounts else str(i)):
        if aid not in accounts:
            issue('missing_account', 'Asset account does not belong to company', account_id=aid)
            continue
        e, actual_balance = expected[aid], actual[aid]
        diff = FixedAssetGLBalance(**{field: getattr(actual_balance, field)-getattr(e, field) for field in FixedAssetGLBalance.model_fields})
        matched = not any(diff.model_dump().values())
        gl_lines.append(FixedAssetGLLine(account_id=aid, account_code=accounts[aid].code, account_name=accounts[aid].name,
            expected=e, actual=actual_balance, difference=diff, matched=matched))
        if not matched:
            issue('account_difference', 'FA subledger differs from GL balance or gross turnover', account_id=aid)
    for a in assets.values():
        related = [e for e in events if e.fixed_asset_id == a.id]
        if a.in_service_date and a.in_service_date <= date_to and not any(e.event_type in ('opening', 'commissioning') for e in related):
            issue('missing_asset_basis', 'In-service asset has no commissioning or opening evidence', fixed_asset_id=a.id)
        cost = sum((e.cost_change for e in related), ZERO)
        accumulated = sum((e.accumulated_change for e in related), ZERO)
        if cost < 0 or accumulated < 0 or cost < accumulated:
            issue('invalid_asset_balance', 'Reconstructed asset balance is negative', fixed_asset_id=a.id)
    return assets, sorted(events, key=lambda e: (e.event_date, e.fixed_asset_id, e.event_type, e.event_id)), gl_lines, issues


async def get_fixed_asset_movement(db, *, company_id, date_from, date_to, fixed_asset_id=None):
    assets, events, gl, issues = await _build(db, company_id, date_from, date_to)
    if fixed_asset_id is not None and fixed_asset_id not in assets:
        raise ValueError('fixed asset not found for company')
    lines = []
    totals = FixedAssetMovementTotals()
    for a in sorted(assets.values(), key=lambda a: (a.asset_number, a.id)):
        if fixed_asset_id is not None and a.id != fixed_asset_id:
            continue
        related = [e for e in events if e.fixed_asset_id == a.id]
        if not related and a.acquisition_date > date_to:
            continue
        line = FixedAssetMovementLine(fixed_asset_id=a.id, asset_number=a.asset_number, current_name=a.name,
            current_status=a.status.value, asset_account_id=a.asset_account_id,
            accumulated_depreciation_account_id=a.accumulated_depreciation_account_id)
        for e in related:
            if e.event_date < date_from:
                line.opening_cost += e.cost_change
                line.opening_accumulated += e.accumulated_change
            else:
                line.cost_increase += max(e.cost_change, ZERO)
                line.cost_decrease += max(-e.cost_change, ZERO)
                line.accumulated_increase += max(e.accumulated_change, ZERO)
                line.accumulated_decrease += max(-e.accumulated_change, ZERO)
        line.opening_net = line.opening_cost - line.opening_accumulated
        line.closing_cost = line.opening_cost + line.cost_increase - line.cost_decrease
        line.closing_accumulated = line.opening_accumulated + line.accumulated_increase - line.accumulated_decrease
        line.closing_net = line.closing_cost - line.closing_accumulated
        lines.append(line)
        for field in FixedAssetMovementTotals.model_fields:
            setattr(totals, field, getattr(totals, field)+getattr(line, field))
    selected_issues = [i for i in issues if fixed_asset_id is None or i.fixed_asset_id in (None, fixed_asset_id)]
    return FixedAssetMovementReport(company_id=company_id, date_from=date_from, date_to=date_to,
        lines=lines, totals=totals, events=[e for e in events if e.event_date >= date_from and (fixed_asset_id is None or e.fixed_asset_id == fixed_asset_id)],
        issues=selected_issues, reconciled=not selected_issues)


async def get_fixed_asset_gl_reconciliation(db, *, company_id, date_from, date_to):
    assets, events, lines, issues = await _build(db, company_id, date_from, date_to)
    return FixedAssetGLReconciliation(company_id=company_id, date_from=date_from, date_to=date_to,
        lines=lines, issues=issues, matched=not issues)


async def get_fixed_asset_depreciation_report(db, **kwargs):
    movement = await get_fixed_asset_movement(db, **kwargs)
    events = [e for e in movement.events if e.event_type == 'depreciation']
    charged = sum((e.amount for e in events if e.reversal_of_id is None), ZERO)
    reversed_amount = -sum((e.amount for e in events if e.reversal_of_id is not None), ZERO)
    return FixedAssetDepreciationReport(company_id=movement.company_id, date_from=movement.date_from, date_to=movement.date_to,
        events=events, charged=charged, reversed=reversed_amount, net=charged-reversed_amount,
        issues=movement.issues, reconciled=movement.reconciled)
