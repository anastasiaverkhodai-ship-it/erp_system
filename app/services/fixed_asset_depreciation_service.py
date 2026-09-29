from __future__ import annotations

from app.services.fixed_asset_lifecycle_guard import lock_company, require_chronology, require_no_later_events, event_time

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, and_
from sqlalchemy.orm import aliased
from calendar import monthrange
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixed_asset import (
    FixedAsset,
    FixedAssetDepreciationMethod,
    FixedAssetStatus,
)
from app.models.fixed_asset_depreciation import (
    FixedAssetDepreciation,
)
from app.models.fixed_asset_opening_balance import (
    FixedAssetOpeningBalance,
)
from app.models.journal_entry import (
    JournalEntry,
JournalEntryStatus,
)
from app.models.journal_entry_line import JournalEntryLine
from app.services.accounting_period_service import (
    ensure_period_open,
)

from app.services.accounting_posting import post_journal_entry
from app.services.accounting_reversal import reverse_journal_entry


MONEY = Decimal("0.01")


class FixedAssetDepreciationError(ValueError):
    pass


def _money(value: Decimal) -> Decimal:
    return value.quantize(
        MONEY,
        rounding=ROUND_HALF_UP,
    )


def _months_between_inclusive(
    start: date,
    end: date,
) -> int:
    if end < start:
        raise FixedAssetDepreciationError(
            "period_end cannot precede period_start"
        )

    return (
        (end.year - start.year) * 12
        + end.month
        - start.month
        + 1
    )


async def _opening_accumulated(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
) -> Decimal:
    from app.models.opening_balance import OpeningBalance
    row = await db.scalar(
        select(FixedAssetOpeningBalance).join(OpeningBalance,
            OpeningBalance.id == FixedAssetOpeningBalance.opening_balance_id).join(
            JournalEntry, JournalEntry.id == OpeningBalance.journal_entry_id).where(
            JournalEntry.status == JournalEntryStatus.POSTED,
            FixedAssetOpeningBalance.company_id
            == company_id,
            FixedAssetOpeningBalance.fixed_asset_id
            == fixed_asset_id,
        )
    )

    if row is None:
        return Decimal("0.00")

    return _money(
        Decimal(row.accumulated_depreciation)
    )


async def _posted_active_total(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
) -> Decimal:
    rows = (
        await db.scalars(
            select(FixedAssetDepreciation).where(
                FixedAssetDepreciation.company_id
                == company_id,
                FixedAssetDepreciation.fixed_asset_id
                == fixed_asset_id,
                FixedAssetDepreciation.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).all()

    total = Decimal("0.00")

    for row in rows:
        reversal = await db.scalar(
            select(FixedAssetDepreciation.id).where(
                FixedAssetDepreciation.company_id
                == company_id,
                FixedAssetDepreciation.reversal_of_id
                == row.id,
            )
        )

        if reversal is None:
            total += Decimal(row.amount)

    return _money(total)


def _next_month(value):
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def _calculate_amount(*, asset, accumulated_before, period_start, period_end,
                      actual_output=None, schedule_start=None, schedule_months=None,
                      schedule_base=None, year_start_carrying=None):
    original_cost = _money(Decimal(asset.original_cost))
    salvage = _money(Decimal(asset.salvage_value))
    base = original_cost - salvage if schedule_base is None else schedule_base
    remaining = _money(original_cost - salvage - accumulated_before)
    method = asset.depreciation_method
    if method == FixedAssetDepreciationMethod.PRODUCTION:
        expected = asset.expected_output
        if expected is None or Decimal(expected) <= 0:
            raise FixedAssetDepreciationError("Production depreciation requires expected total output in the asset card")
        if actual_output is None or not actual_output.is_finite() or actual_output < 0:
            raise FixedAssetDepreciationError("Production depreciation requires nonnegative monthly actual output")
        if remaining < 0:
            raise FixedAssetDepreciationError("Accumulated depreciation exceeds the depreciable base")
        return min(remaining, _money(base * actual_output / Decimal(expected)))
    if actual_output is not None:
        raise FixedAssetDepreciationError("Actual output is only supported for production depreciation")
    if remaining <= 0:
        raise FixedAssetDepreciationError("asset has no remaining depreciable amount")
    life = schedule_months or asset.useful_life_months
    start = schedule_start or _next_month(asset.in_service_date)
    elapsed = (period_start.year - start.year) * 12 + period_start.month - start.month
    if elapsed < 0:
        raise FixedAssetDepreciationError("Depreciation begins in the month following commissioning")
    if method == FixedAssetDepreciationMethod.STRAIGHT_LINE:
        amount = base / Decimal(life)
    elif method in (FixedAssetDepreciationMethod.DIMINISHING_BALANCE,
                    FixedAssetDepreciationMethod.DOUBLE_DIMINISHING_BALANCE):
        carrying = year_start_carrying if year_start_carrying is not None else original_cost - accumulated_before
        if method == FixedAssetDepreciationMethod.DIMINISHING_BALANCE:
            rate = Decimal(1) - (salvage / (base + salvage)) ** (Decimal(12) / Decimal(life))
        else:
            rate = min(Decimal(1), Decimal(24) / Decimal(life))
        amount = carrying * rate / Decimal(12)
    elif method == FixedAssetDepreciationMethod.CUMULATIVE:
        years = (life + 11) // 12
        full_years, tail_months = divmod(life, 12)
        denominator = 12 * (full_years * years - full_years * (full_years - 1) // 2) + tail_months * (years - full_years)
        weight = max(1, years - elapsed // 12)
        amount = base * Decimal(weight) / Decimal(denominator)
    else:
        raise FixedAssetDepreciationError("unsupported depreciation method")
    # Close any rounding remainder at the end of the useful life.
    if elapsed >= life - 1:
        amount = remaining
    amount = min(remaining, _money(amount))
    if amount <= 0:
        raise FixedAssetDepreciationError("calculated depreciation must be positive")
    return amount


async def _schedule_inputs(db, asset, period_start, accumulated_before):
    from app.models.fixed_asset_repair_improvement import FixedAssetRepairImprovement as Improvement
    undo = aliased(Improvement)
    change = await db.scalar(select(Improvement).where(
        Improvement.company_id == asset.company_id, Improvement.fixed_asset_id == asset.id,
        Improvement.new_remaining_life_months.is_not(None), Improvement.reversal_of_id.is_(None),
        ~select(undo.id).where(undo.reversal_of_id == Improvement.id).exists()
    ).order_by(Improvement.operation_date.desc(), Improvement.created_at.desc()).limit(1))
    from app.models.fixed_asset_disposal import FixedAssetDisposal
    from datetime import timezone
    undo_disposal = aliased(FixedAssetDisposal)
    partials = (await db.scalars(select(FixedAssetDisposal).where(
        FixedAssetDisposal.company_id == asset.company_id,
        FixedAssetDisposal.fixed_asset_id == asset.id,
        FixedAssetDisposal.disposal_fraction < 1,
        FixedAssetDisposal.reversal_of_id.is_(None),
        ~select(undo_disposal.id).where(undo_disposal.reversal_of_id == FixedAssetDisposal.id).exists()
    ))).all()
    def retained_fraction(created_at):
        stamp = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
        factor = Decimal(1)
        for disposal in partials:
            if disposal.created_at.replace(tzinfo=timezone.utc) >= stamp:
                factor *= 1 - Decimal(disposal.disposal_fraction)
        return factor
    start = _next_month(asset.in_service_date)
    life = asset.useful_life_months
    base = Decimal(asset.original_cost) - Decimal(asset.salvage_value)
    if change is not None:
        effective = _next_month(change.operation_date)
        if period_start >= effective:
            start = effective
            life = change.new_remaining_life_months
            base = Decimal(asset.original_cost) - Decimal(asset.salvage_value) - _money(Decimal(change.accumulated_at_change) * retained_fraction(change.created_at))
        else:
            life = change.useful_life_months_before
            base -= Decimal(change.amount)
    undo_dep = aliased(FixedAssetDepreciation)
    rows = (await db.scalars(select(FixedAssetDepreciation).where(
        FixedAssetDepreciation.company_id == asset.company_id,
        FixedAssetDepreciation.fixed_asset_id == asset.id,
        FixedAssetDepreciation.reversal_of_id.is_(None),
        ~select(undo_dep.id).where(undo_dep.reversal_of_id == FixedAssetDepreciation.id).exists()
    ))).all()
    if change is not None and period_start >= _next_month(change.operation_date):
        # The change takes effect next month; later posting of that month's
        # old-schedule depreciation must reduce the prospective opening base.
        base -= _money(sum((Decimal(row.amount) * retained_fraction(row.created_at) for row in rows
                     if row.created_at > change.created_at and row.period_start < start), Decimal(0)))
    year_begin = max(date(period_start.year, 1, 1), start)
    charged_this_year = _money(sum((Decimal(row.amount) * retained_fraction(row.created_at) for row in rows
                            if year_begin <= row.period_start < period_start), Decimal(0)))
    return dict(schedule_start=start, schedule_months=life, schedule_base=base,
                year_start_carrying=Decimal(asset.original_cost) - accumulated_before + charged_this_year)


async def create_and_post_depreciation(
    *,
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
    request_key: str,
    period_start: date,
    period_end: date,
    posting_date: date,
    created_by: int,
    actual_output: Decimal | None = None,
) -> FixedAssetDepreciation:
    await lock_company(db, company_id)
    if actual_output is not None:
        actual_output = Decimal(actual_output)
        if not actual_output.is_finite() or actual_output < 0 or actual_output != actual_output.quantize(Decimal("0.000001")):
            raise FixedAssetDepreciationError("Actual output must be nonnegative with at most six decimal places")
    request_key = request_key.strip()

    if (
        not request_key
        or len(request_key) > 100
        or request_key.startswith("reversal:")
    ):
        raise FixedAssetDepreciationError(
            "invalid request_key"
        )

    existing = await db.scalar(
        select(FixedAssetDepreciation).where(
            FixedAssetDepreciation.company_id
            == company_id,
            FixedAssetDepreciation.request_key
            == request_key,
        )
    )

    if existing is not None:
        if (
            existing.fixed_asset_id
            == fixed_asset_id
            and existing.period_start
            == period_start
            and existing.period_end
            == period_end
            and existing.posting_date
            == posting_date
            and existing.actual_output == actual_output
            and existing.reversal_of_id is None
        ):
            return existing

        raise FixedAssetDepreciationError(
            "request_key already belongs to "
            "a different request"
        )

    if period_end < period_start:
        raise FixedAssetDepreciationError(
            "period_end cannot precede period_start"
        )

    if posting_date < period_end:
        raise FixedAssetDepreciationError(
            "posting_date cannot precede period_end"
        )

    if posting_date > date.today():
        raise FixedAssetDepreciationError(
            "posting_date cannot be in the future"
        )

    await ensure_period_open(
        company_id=company_id,
        operation_date=posting_date,
        db=db,
    )

    asset = await db.scalar(
        select(FixedAsset)
        .where(
            FixedAsset.company_id == company_id,
            FixedAsset.id == fixed_asset_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if asset is None:
        raise FixedAssetDepreciationError(
            "fixed asset not found for company"
        )

    if asset.status != FixedAssetStatus.IN_SERVICE:
        raise FixedAssetDepreciationError(
            "fixed asset must be in service"
        )

    if asset.in_service_date is None:
        raise FixedAssetDepreciationError(
            "fixed asset has no in-service date"
        )

    if period_start < asset.in_service_date:
        raise FixedAssetDepreciationError(
            "depreciation period cannot precede "
            "in-service date"
        )

    if period_start.day != 1 or period_end != date(period_start.year, period_start.month,
            monthrange(period_start.year, period_start.month)[1]):
        raise FixedAssetDepreciationError("Depreciation requires one complete calendar month")
    await require_chronology(db, asset, posting_date)
    reversed_event = aliased(FixedAssetDepreciation)
    duplicate = await db.scalar(select(FixedAssetDepreciation.id).where(
        FixedAssetDepreciation.company_id == company_id,
        FixedAssetDepreciation.fixed_asset_id == fixed_asset_id,
        FixedAssetDepreciation.period_start <= period_end,
        FixedAssetDepreciation.period_end >= period_start,
        FixedAssetDepreciation.reversal_of_id.is_(None),
        ~select(reversed_event.id).where(reversed_event.reversal_of_id == FixedAssetDepreciation.id).exists()
    ).limit(1))
    if duplicate is not None:
        raise FixedAssetDepreciationError("depreciation already exists for overlapping period")
    later_period = await db.scalar(select(FixedAssetDepreciation.id).where(
        FixedAssetDepreciation.company_id == company_id,
        FixedAssetDepreciation.fixed_asset_id == fixed_asset_id,
        FixedAssetDepreciation.period_start > period_end,
        FixedAssetDepreciation.reversal_of_id.is_(None),
        ~select(reversed_event.id).where(reversed_event.reversal_of_id == FixedAssetDepreciation.id).exists()
    ).limit(1))
    if later_period is not None:
        raise FixedAssetDepreciationError("Reverse later depreciation periods before charging an earlier month")
    from app.models.opening_balance import OpeningBalance
    cutover = await db.scalar(select(OpeningBalance.opening_date).join(
        FixedAssetOpeningBalance, FixedAssetOpeningBalance.opening_balance_id == OpeningBalance.id
    ).join(JournalEntry, JournalEntry.id == OpeningBalance.journal_entry_id).where(
        FixedAssetOpeningBalance.company_id == company_id,
        FixedAssetOpeningBalance.fixed_asset_id == fixed_asset_id,
        JournalEntry.status == JournalEntryStatus.POSTED))
    if cutover is not None and period_start < cutover:
        raise FixedAssetDepreciationError("Depreciation period cannot precede opening cutover")

    # Suspended intervals must not be charged using only the current card status.
    from app.models.fixed_asset import FixedAssetCardHistory
    history = (await db.scalars(select(FixedAssetCardHistory).where(
        FixedAssetCardHistory.company_id == company_id,
        FixedAssetCardHistory.fixed_asset_id == fixed_asset_id,
        FixedAssetCardHistory.effective_date <= period_end
    ).order_by(FixedAssetCardHistory.effective_date, FixedAssetCardHistory.created_at))).all()
    state = FixedAssetStatus.IN_SERVICE
    for change in history:
        if change.effective_date < period_start:
            state = change.status or state
        elif state == FixedAssetStatus.SUSPENDED or change.status == FixedAssetStatus.SUSPENDED:
            raise FixedAssetDepreciationError("Depreciation period overlaps suspended operation")
        else:
            state = change.status or state
    if state == FixedAssetStatus.SUSPENDED:
        raise FixedAssetDepreciationError("Depreciation period overlaps suspended operation")

    opening_accumulated = (
        await _opening_accumulated(
            db,
            company_id,
            fixed_asset_id,
        )
    )

    posted_total = await _posted_active_total(
        db,
        company_id,
        fixed_asset_id,
    )

    accumulated_before = _money(
        opening_accumulated + posted_total - await _disposed_accumulated(db, company_id, fixed_asset_id)
    )

    schedule = await _schedule_inputs(db, asset, period_start, accumulated_before)
    amount = _calculate_amount(
        asset=asset,
        accumulated_before=accumulated_before,
        period_start=period_start,
        period_end=period_end,
        actual_output=actual_output,
        **schedule,
    )

    accumulated_after = _money(
        accumulated_before + amount
    )

    maximum_accumulated = _money(
        Decimal(asset.original_cost)
        - Decimal(asset.salvage_value)
    )

    if accumulated_after > maximum_accumulated:
        raise FixedAssetDepreciationError(
            "depreciation would cross salvage floor"
        )

    depreciation = FixedAssetDepreciation(
        created_at=event_time(),
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        request_key=request_key,
        period_start=period_start,
        period_end=period_end,
        posting_date=posting_date,
        method=asset.depreciation_method.value,
        actual_output=actual_output,
        expected_output=asset.expected_output if actual_output is not None else None,
        amount=amount,
        accumulated_before=accumulated_before,
        accumulated_after=accumulated_after,
        created_by=created_by,
    )

    db.add(depreciation)
    await db.flush()

    if amount == 0:
        return depreciation

    entry = JournalEntry(
        company_id=company_id,
        fixed_asset_depreciation_id=depreciation.id,
        entry_date=posting_date,
        description=(
            "Fixed asset depreciation "
            f"{asset.asset_number}"
        ),
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )

    db.add(entry)
    await db.flush()

    db.add_all(
        [
            JournalEntryLine(
                journal_entry_id=entry.id,
                line_no=1,
                account_id=asset.depreciation_expense_account_id,
                debit=amount,
                credit=Decimal("0.00"),
                description="Fixed asset depreciation expense",
            ),
            JournalEntryLine(
                journal_entry_id=entry.id,
                line_no=2,
                account_id=asset.accumulated_depreciation_account_id,
                debit=Decimal("0.00"),
                credit=amount,
                description="Accumulated depreciation",
            ),
        ]
    )

    await db.flush()

    previous = db.info.get("fixed_asset_depreciation")
    db.info["fixed_asset_depreciation"] = depreciation.id
    try:
        await post_journal_entry(db, company_id, entry.id)
    finally:
        if previous is None:
            db.info.pop("fixed_asset_depreciation", None)
        else:
            db.info["fixed_asset_depreciation"] = previous

    await db.flush()

    return depreciation


async def reverse_depreciation(
    *,
    db: AsyncSession,
    company_id: int,
    depreciation_id: int,
    fixed_asset_id: int | None = None,
    reversal_date: date,
    reversed_by: int,
) -> FixedAssetDepreciation:
    await lock_company(db, company_id)
    original = await db.scalar(
        select(FixedAssetDepreciation)
        .where(
            FixedAssetDepreciation.company_id
            == company_id,
            FixedAssetDepreciation.id
            == depreciation_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if original is None:
        raise FixedAssetDepreciationError(
            "depreciation not found for company"
        )

    if fixed_asset_id is not None and original.fixed_asset_id != fixed_asset_id:
        raise FixedAssetDepreciationError("depreciation not found for asset")
    if original.reversal_of_id is not None:
        raise FixedAssetDepreciationError(
            "a reversal depreciation cannot be reversed"
        )

    existing_reversal = await db.scalar(
        select(FixedAssetDepreciation).where(
            FixedAssetDepreciation.company_id
            == company_id,
            FixedAssetDepreciation.reversal_of_id
            == original.id,
        )
    )

    if existing_reversal is not None:
        if existing_reversal.posting_date != reversal_date:
            raise FixedAssetDepreciationError(
                "depreciation already reversed "
                "on a different date"
            )

        return existing_reversal

    if reversal_date < original.posting_date:
        raise FixedAssetDepreciationError(
            "reversal date cannot precede "
            "original posting date"
        )

    if reversal_date > date.today():
        raise FixedAssetDepreciationError(
            "reversal date cannot be in the future"
        )

    await ensure_period_open(
        company_id=company_id,
        operation_date=reversal_date,
        db=db,
    )

    journal = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.fixed_asset_depreciation_id
            == original.id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )

    if journal is None and original.amount != 0:
        raise FixedAssetDepreciationError(
            "depreciation journal not found"
        )

    if journal is not None and journal.status != JournalEntryStatus.POSTED:
        raise FixedAssetDepreciationError(
            "depreciation journal must be posted"
        )

    asset = await db.scalar(select(FixedAsset).where(
        FixedAsset.company_id == company_id, FixedAsset.id == original.fixed_asset_id
    ).with_for_update().execution_options(populate_existing=True))
    await require_chronology(db, asset, reversal_date)
    await require_no_later_events(db, company_id, original.fixed_asset_id, original)
    if asset.status == FixedAssetStatus.DISPOSED:
        raise FixedAssetDepreciationError("Reverse disposal before depreciation")

    reversal = FixedAssetDepreciation(
        created_at=event_time(),
        company_id=company_id,
        fixed_asset_id=original.fixed_asset_id,
        request_key=f"reversal:{original.id}",
        period_start=original.period_start,
        period_end=original.period_end,
        posting_date=reversal_date,
        method=original.method,
        actual_output=original.actual_output,
        expected_output=original.expected_output,
        amount=original.amount,
        accumulated_before=original.accumulated_after,
        accumulated_after=original.accumulated_before,
        reversal_of_id=original.id,
        created_by=reversed_by,
    )

    db.add(reversal)
    await db.flush()

    if journal is None:
        return reversal

    previous = db.info.get("fixed_asset_depreciation")
    db.info["fixed_asset_depreciation"] = original.id
    try:
        reversal_entry = await reverse_journal_entry(db=db, company_id=company_id,
            journal_entry_id=journal.id, reversal_date=reversal_date, reversed_by=reversed_by)
    finally:
        if previous is None:
            db.info.pop("fixed_asset_depreciation", None)
        else:
            db.info["fixed_asset_depreciation"] = previous

    reversal_entry.fixed_asset_depreciation_id = reversal.id

    await db.flush()

    return reversal


async def _disposed_accumulated(db, company_id, fixed_asset_id):
    from app.models.fixed_asset_disposal import FixedAssetDisposal
    from sqlalchemy import func
    reversal = aliased(FixedAssetDisposal)
    value = await db.scalar(select(func.coalesce(func.sum(FixedAssetDisposal.accumulated_depreciation), 0)).where(
        FixedAssetDisposal.company_id == company_id,
        FixedAssetDisposal.fixed_asset_id == fixed_asset_id,
        FixedAssetDisposal.disposal_fraction < 1,
        FixedAssetDisposal.reversal_of_id.is_(None),
        ~select(reversal.id).where(reversal.reversal_of_id == FixedAssetDisposal.id).exists()))
    return _money(Decimal(value))
