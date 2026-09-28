from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.fixed_asset import FixedAsset, FixedAssetStatus
from app.models.fixed_asset_revaluation_impairment import (
    FixedAssetRevaluationImpairment,
    FixedAssetRevaluationImpairmentType,
)
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.schemas.fixed_asset_revaluation_impairment import (
    FixedAssetRevaluationImpairmentCreate,
)
from app.services.accounting_period_service import ensure_period_open
from app.services.accounting_posting import post_journal_entry
from app.services.accounting_reversal import reverse_journal_entry
from app.services.fixed_asset_depreciation_service import (
    _opening_accumulated,
    _posted_active_total,
)


class FixedAssetRevaluationImpairmentError(ValueError):
    pass


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


async def _get_asset_for_update(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
) -> FixedAsset:
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
        raise FixedAssetRevaluationImpairmentError(
            "fixed asset not found"
        )
    return asset


async def _accumulated(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
) -> Decimal:
    opening = await _opening_accumulated(
        db,
        company_id,
        fixed_asset_id,
    )
    posted = await _posted_active_total(
        db,
        company_id,
        fixed_asset_id,
    )
    return _money(Decimal(opening) + Decimal(posted))


async def list_fixed_asset_revaluation_impairments(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
) -> list[FixedAssetRevaluationImpairment]:
    await _get_asset_for_update(db, company_id, fixed_asset_id)

    result = await db.execute(
        select(FixedAssetRevaluationImpairment)
        .where(
            FixedAssetRevaluationImpairment.company_id == company_id,
            FixedAssetRevaluationImpairment.fixed_asset_id
            == fixed_asset_id,
        )
        .order_by(
            FixedAssetRevaluationImpairment.operation_date,
            FixedAssetRevaluationImpairment.id,
        )
    )
    return list(result.scalars().all())


async def create_fixed_asset_revaluation_impairment(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
    data: FixedAssetRevaluationImpairmentCreate,
    created_by: int,
) -> FixedAssetRevaluationImpairment:
    await ensure_period_open(
        company_id=company_id,
        operation_date=data.operation_date,
        db=db,
    )

    asset = await _get_asset_for_update(
        db,
        company_id,
        fixed_asset_id,
    )

    if asset.status == FixedAssetStatus.DISPOSED:
        raise FixedAssetRevaluationImpairmentError(
            "Disposed fixed asset cannot receive "
            "revaluation or impairment operations"
        )

    existing = await db.scalar(
        select(FixedAssetRevaluationImpairment).where(
            FixedAssetRevaluationImpairment.company_id == company_id,
            FixedAssetRevaluationImpairment.request_key
            == data.request_key,
        )
    )

    target = _money(data.carrying_amount_after)

    if existing is not None:
        same = (
            existing.fixed_asset_id == fixed_asset_id
            and existing.operation_type == data.operation_type
            and existing.operation_date == data.operation_date
            and _money(existing.carrying_amount_after) == target
            and existing.counterpart_account_id
            == data.counterpart_account_id
            and (existing.description or None)
            == (data.description or None)
            and existing.reversal_of_id is None
        )
        if not same:
            raise FixedAssetRevaluationImpairmentError(
                "request key already used with different payload"
            )
        return existing

    account = await db.scalar(
        select(Account).where(
            Account.company_id == company_id,
            Account.id == data.counterpart_account_id,
        )
    )

    if account is None:
        raise FixedAssetRevaluationImpairmentError(
            "counterpart account not found"
        )

    if not account.is_active or not account.is_postable:
        raise FixedAssetRevaluationImpairmentError(
            "counterpart account must be active and postable"
        )

    accumulated = await _accumulated(
        db,
        company_id,
        fixed_asset_id,
    )

    original_cost_before = _money(asset.original_cost)
    carrying_before = _money(
        max(
            original_cost_before - accumulated,
            Decimal("0.00"),
        )
    )

    if target == carrying_before:
        raise FixedAssetRevaluationImpairmentError(
            "new carrying amount must differ from current carrying amount"
        )

    if (
        data.operation_type
        == FixedAssetRevaluationImpairmentType.IMPAIRMENT
        and target >= carrying_before
    ):
        raise FixedAssetRevaluationImpairmentError(
            "impairment must reduce carrying amount"
        )

    amount = _money(abs(target - carrying_before))

    original_cost_after = _money(target + accumulated)

    if original_cost_after < Decimal(asset.salvage_value):
        raise FixedAssetRevaluationImpairmentError(
            "resulting cost cannot be below salvage value"
        )

    row = FixedAssetRevaluationImpairment(
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        operation_type=data.operation_type,
        operation_date=data.operation_date,
        carrying_amount_before=carrying_before,
        carrying_amount_after=target,
        amount=amount,
        accumulated_depreciation=accumulated,
        original_cost_before=original_cost_before,
        original_cost_after=original_cost_after,
        counterpart_account_id=data.counterpart_account_id,
        request_key=data.request_key,
        description=data.description,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()

    upward = target > carrying_before

    journal = JournalEntry(
        company_id=company_id,
        fixed_asset_revaluation_impairment_id=row.id,
        entry_date=data.operation_date,
        description=(
            data.description
            or (
                f"Fixed asset {data.operation_type.value} "
                f"{asset.asset_number}"
            )
        ),
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )
    db.add(journal)
    await db.flush()

    if upward:
        lines = [
            JournalEntryLine(
                line_no=1,
                journal_entry_id=journal.id,
                account_id=asset.asset_account_id,
                debit=amount,
                credit=Decimal("0.00"),
            ),
            JournalEntryLine(
                line_no=2,
                journal_entry_id=journal.id,
                account_id=data.counterpart_account_id,
                debit=Decimal("0.00"),
                credit=amount,
            ),
        ]
    else:
        lines = [
            JournalEntryLine(
                line_no=1,
                journal_entry_id=journal.id,
                account_id=data.counterpart_account_id,
                debit=amount,
                credit=Decimal("0.00"),
            ),
            JournalEntryLine(
                line_no=2,
                journal_entry_id=journal.id,
                account_id=asset.asset_account_id,
                debit=Decimal("0.00"),
                credit=amount,
            ),
        ]

    db.add_all(lines)
    await db.flush()

    previous = db.info.get(
        "fixed_asset_revaluation_impairment"
    )
    db.info["fixed_asset_revaluation_impairment"] = row.id

    try:
        await post_journal_entry(
            db,
            company_id,
            journal.id,
        )
    finally:
        if previous is None:
            db.info.pop(
                "fixed_asset_revaluation_impairment",
                None,
            )
        else:
            db.info[
                "fixed_asset_revaluation_impairment"
            ] = previous

    row.journal_entry_id = journal.id
    asset.original_cost = original_cost_after

    await db.flush()
    return row


async def reverse_fixed_asset_revaluation_impairment(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
    operation_id: int,
    reversal_date: date,
    request_key: str,
    reversed_by: int,
) -> FixedAssetRevaluationImpairment:
    await ensure_period_open(
        company_id=company_id,
        operation_date=reversal_date,
        db=db,
    )

    asset = await _get_asset_for_update(
        db,
        company_id,
        fixed_asset_id,
    )

    existing = await db.scalar(
        select(FixedAssetRevaluationImpairment).where(
            FixedAssetRevaluationImpairment.company_id == company_id,
            FixedAssetRevaluationImpairment.request_key == request_key,
        )
    )

    if existing is not None:
        same = (
            existing.fixed_asset_id == fixed_asset_id
            and existing.reversal_of_id == operation_id
            and existing.operation_date == reversal_date
        )
        if not same:
            raise FixedAssetRevaluationImpairmentError(
                "request key already used with different reversal payload"
            )
        return existing

    original = await db.scalar(
        select(FixedAssetRevaluationImpairment)
        .where(
            FixedAssetRevaluationImpairment.company_id == company_id,
            FixedAssetRevaluationImpairment.fixed_asset_id
            == fixed_asset_id,
            FixedAssetRevaluationImpairment.id == operation_id,
        )
        .with_for_update()
    )

    if original is None:
        raise FixedAssetRevaluationImpairmentError(
            "revaluation/impairment operation not found"
        )

    if original.reversal_of_id is not None:
        raise FixedAssetRevaluationImpairmentError(
            "cannot reverse a reversal"
        )

    if reversal_date < original.operation_date:
        raise FixedAssetRevaluationImpairmentError(
            "Reversal date cannot precede original operation date"
        )

    prior_reversal = await db.scalar(
        select(FixedAssetRevaluationImpairment.id).where(
            FixedAssetRevaluationImpairment.company_id == company_id,
            FixedAssetRevaluationImpairment.reversal_of_id
            == original.id,
        )
    )
    if prior_reversal is not None:
        raise FixedAssetRevaluationImpairmentError(
            "operation already reversed"
        )

    current_cost = _money(asset.original_cost)

    if current_cost != _money(original.original_cost_after):
        raise FixedAssetRevaluationImpairmentError(
            "cannot reverse because later asset valuation changes exist"
        )

    journal = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.id == original.journal_entry_id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )

    if journal is None or journal.status != JournalEntryStatus.POSTED:
        raise FixedAssetRevaluationImpairmentError(
            "original valuation journal must be posted"
        )

    reversal = FixedAssetRevaluationImpairment(
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        operation_type=original.operation_type,
        operation_date=reversal_date,
        carrying_amount_before=original.carrying_amount_after,
        carrying_amount_after=original.carrying_amount_before,
        amount=original.amount,
        accumulated_depreciation=original.accumulated_depreciation,
        original_cost_before=original.original_cost_after,
        original_cost_after=original.original_cost_before,
        counterpart_account_id=original.counterpart_account_id,
        request_key=request_key,
        description=f"Reversal of valuation operation {original.id}",
        reversal_of_id=original.id,
        created_by=reversed_by,
    )
    db.add(reversal)
    await db.flush()

    previous = db.info.get(
        "fixed_asset_revaluation_impairment"
    )
    db.info["fixed_asset_revaluation_impairment"] = original.id

    try:
        reversal_entry = await reverse_journal_entry(
            db=db,
            company_id=company_id,
            journal_entry_id=journal.id,
            reversal_date=reversal_date,
            reversed_by=reversed_by,
        )
    finally:
        if previous is None:
            db.info.pop(
                "fixed_asset_revaluation_impairment",
                None,
            )
        else:
            db.info[
                "fixed_asset_revaluation_impairment"
            ] = previous

    reversal_entry.fixed_asset_revaluation_impairment_id = reversal.id
    reversal.journal_entry_id = reversal_entry.id

    asset.original_cost = original.original_cost_before

    await db.flush()
    return reversal
