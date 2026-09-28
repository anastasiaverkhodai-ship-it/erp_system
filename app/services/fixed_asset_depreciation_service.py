from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
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
    row = await db.scalar(
        select(FixedAssetOpeningBalance).where(
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


def _calculate_amount(
    *,
    asset: FixedAsset,
    accumulated_before: Decimal,
    period_start: date,
    period_end: date,
) -> Decimal:
    original_cost = _money(
        Decimal(asset.original_cost)
    )
    salvage_value = _money(
        Decimal(asset.salvage_value)
    )

    depreciable_base = _money(
        original_cost - salvage_value
    )

    remaining = _money(
        depreciable_base - accumulated_before
    )

    if remaining <= Decimal("0.00"):
        raise FixedAssetDepreciationError(
            "asset has no remaining depreciable amount"
        )

    months = _months_between_inclusive(
        period_start,
        period_end,
    )

    method = asset.depreciation_method

    if method == FixedAssetDepreciationMethod.PRODUCTION:
        raise FixedAssetDepreciationError(
            "production depreciation requires "
            "production quantity foundation"
        )

    if method == FixedAssetDepreciationMethod.STRAIGHT_LINE:
        monthly = (
            depreciable_base
            / Decimal(asset.useful_life_months)
        )
        amount = monthly * Decimal(months)

    elif method == FixedAssetDepreciationMethod.DIMINISHING_BALANCE:
        raise FixedAssetDepreciationError(
            "diminishing_balance requires an explicit "
            "depreciation rate contract"
        )

    elif method == FixedAssetDepreciationMethod.DOUBLE_DIMINISHING_BALANCE:
        monthly_rate = (
            Decimal("2")
            / Decimal(asset.useful_life_months)
        )
        carrying_amount = _money(
            original_cost - accumulated_before
        )
        amount = (
            carrying_amount
            * monthly_rate
            * Decimal(months)
        )

    elif method == FixedAssetDepreciationMethod.CUMULATIVE:
        useful_life = int(asset.useful_life_months)

        if useful_life <= 0:
            raise FixedAssetDepreciationError(
                "useful life must be positive"
            )

        elapsed_amount = _money(accumulated_before)

        if depreciable_base <= Decimal("0.00"):
            raise FixedAssetDepreciationError(
                "asset has no depreciable base"
            )

        approximate_elapsed = int(
            (
                Decimal(useful_life)
                * elapsed_amount
                / depreciable_base
            ).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )

        remaining_life = max(
            useful_life - approximate_elapsed,
            1,
        )

        periods_to_charge = min(
            months,
            remaining_life,
        )

        denominator = Decimal(
            remaining_life
            * (remaining_life + 1)
            // 2
        )

        if denominator <= Decimal("0"):
            raise FixedAssetDepreciationError(
                "invalid cumulative depreciation denominator"
            )

        amount = Decimal("0.00")

        for offset in range(periods_to_charge):
            weight = Decimal(
                remaining_life - offset
            )
            amount += (
                remaining
                * weight
                / denominator
            )

    else:
        raise FixedAssetDepreciationError(
            "unsupported depreciation method"
        )

    amount = _money(amount)

    if amount > remaining:
        amount = remaining

    if amount <= Decimal("0.00"):
        raise FixedAssetDepreciationError(
            "calculated depreciation must be positive"
        )

    return amount


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
) -> FixedAssetDepreciation:
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

    duplicate = await db.scalar(
        select(FixedAssetDepreciation).where(
            FixedAssetDepreciation.company_id
            == company_id,
            FixedAssetDepreciation.fixed_asset_id
            == fixed_asset_id,
            FixedAssetDepreciation.period_start
            == period_start,
            FixedAssetDepreciation.period_end
            == period_end,
            FixedAssetDepreciation.reversal_of_id.is_(
                None
            ),
        )
    )

    if duplicate is not None:
        reversed_row = await db.scalar(
            select(FixedAssetDepreciation.id).where(
                FixedAssetDepreciation.company_id
                == company_id,
                FixedAssetDepreciation.reversal_of_id
                == duplicate.id,
            )
        )

        if reversed_row is None:
            raise FixedAssetDepreciationError(
                "depreciation already exists "
                "for asset and period"
            )

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
        opening_accumulated + posted_total
    )

    amount = _calculate_amount(
        asset=asset,
        accumulated_before=accumulated_before,
        period_start=period_start,
        period_end=period_end,
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
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        request_key=request_key,
        period_start=period_start,
        period_end=period_end,
        posting_date=posting_date,
        method=asset.depreciation_method.value,
        amount=amount,
        accumulated_before=accumulated_before,
        accumulated_after=accumulated_after,
        created_by=created_by,
    )

    db.add(depreciation)
    await db.flush()

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

    await post_journal_entry(
        db,
        company_id,
        entry.id,
    )

    await db.flush()

    return depreciation


async def reverse_depreciation(
    *,
    db: AsyncSession,
    company_id: int,
    depreciation_id: int,
    reversal_date: date,
    reversed_by: int,
) -> FixedAssetDepreciation:
    original = await db.scalar(
        select(FixedAssetDepreciation)
        .where(
            FixedAssetDepreciation.company_id
            == company_id,
            FixedAssetDepreciation.id
            == depreciation_id,
        )
        .with_for_update()
    )

    if original is None:
        raise FixedAssetDepreciationError(
            "depreciation not found for company"
        )

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

    if journal is None:
        raise FixedAssetDepreciationError(
            "depreciation journal not found"
        )

    if journal.status != JournalEntryStatus.POSTED:
        raise FixedAssetDepreciationError(
            "depreciation journal must be posted"
        )

    reversal = FixedAssetDepreciation(
        company_id=company_id,
        fixed_asset_id=original.fixed_asset_id,
        request_key=f"reversal:{original.id}",
        period_start=original.period_start,
        period_end=original.period_end,
        posting_date=reversal_date,
        method=original.method,
        amount=original.amount,
        accumulated_before=original.accumulated_after,
        accumulated_after=original.accumulated_after,
        reversal_of_id=original.id,
        created_by=reversed_by,
    )

    db.add(reversal)
    await db.flush()

    reversal_entry = await reverse_journal_entry(
        db=db,
        company_id=company_id,
        journal_entry_id=journal.id,
        reversal_date=reversal_date,
        reversed_by=reversed_by,
    )

    reversal_entry.fixed_asset_depreciation_id = reversal.id

    await db.flush()

    return reversal
