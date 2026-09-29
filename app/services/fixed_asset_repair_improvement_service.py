from app.services.fixed_asset_lifecycle_guard import lock_company, require_chronology, require_no_later_events, event_time
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account, AccountType
from app.models.fixed_asset import FixedAsset
from app.models.fixed_asset_repair_improvement import (
    FixedAssetRepairImprovement,
    FixedAssetRepairImprovementType,
)
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.schemas.fixed_asset_repair_improvement import (
    FixedAssetRepairImprovementCreate,
)
from app.services.accounting_period_service import ensure_period_open
from app.services.accounting_posting import post_journal_entry
from app.services.accounting_reversal import reverse_journal_entry


class FixedAssetRepairImprovementError(ValueError):
    pass


async def _get_asset_for_update(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
) -> FixedAsset:
    result = await db.execute(
        select(FixedAsset)
        .where(
            FixedAsset.company_id == company_id,
            FixedAsset.id == fixed_asset_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise FixedAssetRepairImprovementError("fixed asset not found")
    return asset


async def _get_source_line(
    db: AsyncSession,
    company_id: int,
    line_id: int,
) -> JournalEntryLine:
    result = await db.execute(
        select(JournalEntryLine)
        .join(
            JournalEntry,
            JournalEntry.id == JournalEntryLine.journal_entry_id,
        )
        .where(
            JournalEntry.company_id == company_id,
            JournalEntryLine.id == line_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    line = result.scalar_one_or_none()
    if line is None:
        raise FixedAssetRepairImprovementError(
            "source journal entry line not found"
        )

    entry_result = await db.execute(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.id == line.journal_entry_id,
        )
    )
    entry = entry_result.scalar_one_or_none()
    if entry is None or entry.status != JournalEntryStatus.POSTED:
        raise FixedAssetRepairImprovementError(
            "source journal entry must be posted"
        )

    if entry.reversal_of_id is not None:
        raise FixedAssetRepairImprovementError(
            "source journal entry must be an original posted entry"
        )

    return line, entry


async def _active_improvement_total(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
) -> Decimal:
    originals = (
        select(
            FixedAssetRepairImprovement.id.label("id"),
            FixedAssetRepairImprovement.amount.label("amount"),
        )
        .where(
            FixedAssetRepairImprovement.company_id == company_id,
            FixedAssetRepairImprovement.fixed_asset_id == fixed_asset_id,
            FixedAssetRepairImprovement.operation_type
            == FixedAssetRepairImprovementType.IMPROVEMENT,
            FixedAssetRepairImprovement.reversal_of_id.is_(None),
        )
        .subquery()
    )

    reversals = (
        select(FixedAssetRepairImprovement.reversal_of_id)
        .where(
            FixedAssetRepairImprovement.company_id == company_id,
            FixedAssetRepairImprovement.reversal_of_id.is_not(None),
        )
    )

    result = await db.execute(
        select(func.coalesce(func.sum(originals.c.amount), 0)).where(
            originals.c.id.not_in(reversals)
        )
    )
    return Decimal(result.scalar_one() or 0)


async def list_fixed_asset_repair_improvements(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
) -> list[FixedAssetRepairImprovement]:
    await _get_asset_for_update(db, company_id, fixed_asset_id)
    result = await db.execute(
        select(FixedAssetRepairImprovement)
        .where(
            FixedAssetRepairImprovement.company_id == company_id,
            FixedAssetRepairImprovement.fixed_asset_id == fixed_asset_id,
        )
        .order_by(
            FixedAssetRepairImprovement.operation_date,
            FixedAssetRepairImprovement.id,
        )
    )
    return list(result.scalars().all())


async def create_fixed_asset_repair_improvement(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
    data: FixedAssetRepairImprovementCreate,
    created_by: int,
) -> FixedAssetRepairImprovement:
    await lock_company(db, company_id)
    await ensure_period_open(company_id, data.operation_date, db)

    asset = await _get_asset_for_update(
        db,
        company_id,
        fixed_asset_id,
    )

    asset_status = getattr(asset.status, "value", asset.status)
    if asset_status == "disposed":
        raise FixedAssetRepairImprovementError(
            "Disposed fixed asset cannot receive repair or improvement operations"
        )

    existing_result = await db.execute(
        select(FixedAssetRepairImprovement).where(
            FixedAssetRepairImprovement.company_id == company_id,
            FixedAssetRepairImprovement.request_key == data.request_key,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        same_request = (
            existing.fixed_asset_id == fixed_asset_id
            and existing.new_remaining_life_months == data.new_remaining_life_months
            and existing.operation_type == data.operation_type
            and existing.operation_date == data.operation_date
            and Decimal(existing.amount) == Decimal(data.amount)
            and existing.source_journal_entry_line_id
            == data.source_journal_entry_line_id
            and (existing.description or None)
            == (data.description or None)
            and existing.reversal_of_id is None
        )
        if not same_request:
            raise FixedAssetRepairImprovementError(
                "request key already used with different payload"
            )
        return existing

    await require_chronology(db, asset, data.operation_date)
    if data.new_remaining_life_months is not None and (data.operation_type != FixedAssetRepairImprovementType.IMPROVEMENT or asset.in_service_date is None):
        raise FixedAssetRepairImprovementError("Remaining life can only change with an improvement of an in-service asset")

    source_line, source_entry = await _get_source_line(
        db,
        company_id,
        data.source_journal_entry_line_id,
    )

    if source_entry.entry_date > data.operation_date:
        raise FixedAssetRepairImprovementError(
            "source journal entry date cannot be after operation date"
        )

    amount = Decimal(data.amount)

    source_debit = Decimal(getattr(source_line, "debit", 0) or 0)
    source_credit = Decimal(getattr(source_line, "credit", 0) or 0)

    if source_debit <= 0 or source_credit != 0:
        raise FixedAssetRepairImprovementError(
            "source line must be a posted debit expense line"
        )

    allocated_result = await db.execute(
        select(
            func.coalesce(
                func.sum(FixedAssetRepairImprovement.amount),
                0,
            )
        ).where(
            FixedAssetRepairImprovement.company_id == company_id,
            FixedAssetRepairImprovement.source_journal_entry_line_id
            == data.source_journal_entry_line_id,
            FixedAssetRepairImprovement.reversal_of_id.is_(None),
            ~FixedAssetRepairImprovement.id.in_(
                select(
                    FixedAssetRepairImprovement.reversal_of_id
                ).where(
                    FixedAssetRepairImprovement.company_id == company_id,
                    FixedAssetRepairImprovement.reversal_of_id.is_not(None),
                )
            ),
        )
    )
    allocated = Decimal(allocated_result.scalar_one() or 0)

    if allocated + amount > source_debit:
        raise FixedAssetRepairImprovementError(
            "amount exceeds unallocated source expense line"
        )

    row = FixedAssetRepairImprovement(
        created_at=event_time(),
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        operation_type=data.operation_type,
        operation_date=data.operation_date,
        amount=amount,
        new_remaining_life_months=data.new_remaining_life_months,
        source_journal_entry_line_id=data.source_journal_entry_line_id,
        request_key=data.request_key,
        description=data.description,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()

    if data.operation_type == FixedAssetRepairImprovementType.REPAIR:
        return row

    asset_account_result = await db.execute(
        select(Account).where(
            Account.company_id == company_id,
            Account.id == asset.asset_account_id,
        )
    )
    asset_account = asset_account_result.scalar_one_or_none()
    if asset_account is None:
        raise FixedAssetRepairImprovementError("asset account not found")
    if not asset_account.is_active or not asset_account.is_postable:
        raise FixedAssetRepairImprovementError(
            "asset account must be active and postable"
        )

    source_account_result = await db.execute(
        select(Account).where(
            Account.company_id == company_id,
            Account.id == source_line.account_id,
        )
    )
    source_account = source_account_result.scalar_one_or_none()
    if source_account is None:
        raise FixedAssetRepairImprovementError("source account not found")
    if not source_account.is_active or not source_account.is_postable:
        raise FixedAssetRepairImprovementError(
            "source account must be active and postable"
        )
    if source_account.account_type != AccountType.EXPENSE:
        raise FixedAssetRepairImprovementError(
            "source account must be an expense account"
        )

    journal_entry = JournalEntry(
        company_id=company_id,
        fixed_asset_repair_improvement_id=row.id,
        entry_date=data.operation_date,
        description=(
            data.description
            or f"Fixed asset improvement {asset.asset_number}"
        ),
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )
    db.add(journal_entry)
    await db.flush()

    db.add_all(
        [
            JournalEntryLine(
                line_no=1,
                journal_entry_id=journal_entry.id,
                account_id=asset.asset_account_id,
                debit=amount,
                credit=Decimal("0"),
            ),
            JournalEntryLine(
                line_no=2,
                journal_entry_id=journal_entry.id,
                account_id=source_line.account_id,
                debit=Decimal("0"),
                credit=amount,
            ),
        ]
    )
    await db.flush()

    previous_lifecycle = db.info.get(
        "fixed_asset_repair_improvement"
    )
    db.info["fixed_asset_repair_improvement"] = row.id
    try:
        await post_journal_entry(
            db,
            company_id,
            journal_entry.id,
        )
    finally:
        if previous_lifecycle is None:
            db.info.pop(
                "fixed_asset_repair_improvement",
                None,
            )
        else:
            db.info[
                "fixed_asset_repair_improvement"
            ] = previous_lifecycle

    row.journal_entry_id = journal_entry.id

    if data.new_remaining_life_months is not None:
        from app.services.fixed_asset_revaluation_impairment_service import _accumulated
        row.useful_life_months_before = asset.useful_life_months
        row.accumulated_at_change = await _accumulated(db, company_id, fixed_asset_id)
        row.original_cost_after = Decimal(asset.original_cost) + amount
        row.depreciable_base_after = row.original_cost_after - Decimal(asset.salvage_value) - row.accumulated_at_change
        elapsed = max(0, (data.operation_date.year - asset.in_service_date.year) * 12 + data.operation_date.month - asset.in_service_date.month)
        asset.useful_life_months = elapsed + data.new_remaining_life_months

    asset.original_cost = (
        Decimal(asset.original_cost or 0) + amount
    )

    await db.flush()
    return row


async def reverse_fixed_asset_repair_improvement(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
    operation_id: int,
    reversal_date: date,
    request_key: str,
    reversed_by: int,
) -> FixedAssetRepairImprovement:
    await lock_company(db, company_id)
    await ensure_period_open(company_id, reversal_date, db)

    asset = await _get_asset_for_update(
        db,
        company_id,
        fixed_asset_id,
    )

    idempotent_result = await db.execute(
        select(FixedAssetRepairImprovement).where(
            FixedAssetRepairImprovement.company_id == company_id,
            FixedAssetRepairImprovement.request_key == request_key,
        )
    )
    idempotent = idempotent_result.scalar_one_or_none()
    if idempotent is not None:
        same_reversal = (
            idempotent.fixed_asset_id == fixed_asset_id
            and idempotent.reversal_of_id == operation_id
            and idempotent.operation_date == reversal_date
        )
        if not same_reversal:
            raise FixedAssetRepairImprovementError(
                "request key already used with different reversal payload"
            )
        return idempotent

    result = await db.execute(
        select(FixedAssetRepairImprovement)
        .where(
            FixedAssetRepairImprovement.company_id == company_id,
            FixedAssetRepairImprovement.fixed_asset_id == fixed_asset_id,
            FixedAssetRepairImprovement.id == operation_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    original = result.scalar_one_or_none()

    if original is None:
        raise FixedAssetRepairImprovementError(
            "repair/improvement operation not found"
        )
    if reversal_date < original.operation_date:
        raise FixedAssetRepairImprovementError("Reversal date cannot precede original operation date")
    if original.reversal_of_id is not None:
        raise FixedAssetRepairImprovementError(
            "cannot reverse a reversal"
        )

    prior_reversal = await db.execute(
        select(FixedAssetRepairImprovement.id).where(
            FixedAssetRepairImprovement.company_id == company_id,
            FixedAssetRepairImprovement.reversal_of_id == original.id,
        )
    )
    if prior_reversal.scalar_one_or_none() is not None:
        raise FixedAssetRepairImprovementError(
            "operation is already reversed"
        )

    await require_chronology(db, asset, reversal_date)
    await require_no_later_events(db, company_id, fixed_asset_id, original)

    reversal = FixedAssetRepairImprovement(
        created_at=event_time(),
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        operation_type=original.operation_type,
        operation_date=reversal_date,
        amount=original.amount,
        new_remaining_life_months=original.new_remaining_life_months,
        useful_life_months_before=original.useful_life_months_before,
        depreciable_base_after=original.depreciable_base_after,
        original_cost_after=original.original_cost_after,
        accumulated_at_change=original.accumulated_at_change,
        source_journal_entry_line_id=original.source_journal_entry_line_id,
        request_key=request_key,
        description=f"Reversal: {original.description or original.id}",
        reversal_of_id=original.id,
        created_by=reversed_by,
    )
    db.add(reversal)
    await db.flush()

    if original.operation_type == FixedAssetRepairImprovementType.IMPROVEMENT:
        if original.journal_entry_id is None:
            raise FixedAssetRepairImprovementError(
                "improvement journal entry is missing"
            )

        previous_lifecycle = db.info.get(
            "fixed_asset_repair_improvement"
        )
        db.info["fixed_asset_repair_improvement"] = original.id
        try:
            reversal_entry = await reverse_journal_entry(
                db=db,
                company_id=company_id,
                journal_entry_id=original.journal_entry_id,
                reversal_date=reversal_date,
                reversed_by=reversed_by,
            )
        finally:
            if previous_lifecycle is None:
                db.info.pop(
                    "fixed_asset_repair_improvement",
                    None,
                )
            else:
                db.info[
                    "fixed_asset_repair_improvement"
                ] = previous_lifecycle

        reversal.journal_entry_id = reversal_entry.id

        new_cost = Decimal(asset.original_cost or 0) - Decimal(original.amount)
        if new_cost < Decimal(asset.salvage_value or 0):
            raise FixedAssetRepairImprovementError(
                "reversal would reduce original cost below salvage value"
            )
        asset.original_cost = new_cost
        if original.useful_life_months_before is not None:
            asset.useful_life_months = original.useful_life_months_before

    await db.flush()
    return reversal
