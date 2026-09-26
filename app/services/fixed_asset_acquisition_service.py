from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.account import Account
from app.models.company import Company
from app.models.fixed_asset import FixedAsset
from app.models.fixed_asset_acquisition import (
    FixedAssetAcquisitionCost,
    FixedAssetAcquisitionCostType,
)
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import JournalEntryLine
from app.services.accounting_period_service import ensure_period_open


class FixedAssetAcquisitionError(Exception):
    pass


class FixedAssetAcquisitionNotFoundError(
    FixedAssetAcquisitionError
):
    pass


def _amount(value: Decimal) -> Decimal:
    try:
        result = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise FixedAssetAcquisitionError(
            "amount must be a valid monetary value"
        )

    if not result.is_finite() or result <= 0:
        raise FixedAssetAcquisitionError(
            "amount must be greater than zero"
        )

    return result


def _request_key(value: str) -> str:
    if not isinstance(value, str):
        raise FixedAssetAcquisitionError(
            "request_key must contain 1 to 100 characters"
        )

    value = value.strip()

    if not value or len(value) > 100:
        raise FixedAssetAcquisitionError(
            "request_key must contain 1 to 100 characters"
        )

    return value


def _active_original_condition():
    reversal = aliased(FixedAssetAcquisitionCost)

    return ~select(reversal.id).where(
        reversal.company_id
        == FixedAssetAcquisitionCost.company_id,
        reversal.reversal_of_id
        == FixedAssetAcquisitionCost.id,
    ).exists()


async def _lock_company(
    db: AsyncSession,
    company_id: int,
) -> Company:
    result = await db.execute(
        select(Company)
        .where(
            Company.id == company_id,
            Company.is_active.is_(True),
        )
        .with_for_update()
    )

    company = result.scalar_one_or_none()

    if company is None:
        raise FixedAssetAcquisitionNotFoundError(
            "active company not found"
        )

    return company


async def _require_asset(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
    *,
    for_update: bool = False,
) -> FixedAsset:
    statement = select(FixedAsset).where(
        FixedAsset.company_id == company_id,
        FixedAsset.id == fixed_asset_id,
    )

    if for_update:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )

    result = await db.execute(statement)
    asset = result.scalar_one_or_none()

    if asset is None:
        raise FixedAssetAcquisitionNotFoundError(
            "fixed asset not found"
        )

    return asset


async def _load_source(
    db: AsyncSession,
    company_id: int,
    source_journal_entry_line_id: int,
) -> tuple[JournalEntry, JournalEntryLine, Account]:
    result = await db.execute(
        select(JournalEntryLine)
        .join(
            JournalEntry,
            JournalEntry.id
            == JournalEntryLine.journal_entry_id,
        )
        .where(
            JournalEntryLine.id
            == source_journal_entry_line_id,
            JournalEntry.company_id == company_id,
        )
    )

    source = result.scalar_one_or_none()

    if source is None:
        raise FixedAssetAcquisitionError(
            "accounting source not found in this company"
        )

    journal_result = await db.execute(
        select(JournalEntry)
        .where(
            JournalEntry.id == source.journal_entry_id,
            JournalEntry.company_id == company_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    journal = journal_result.scalar_one()

    source_result = await db.execute(
        select(JournalEntryLine)
        .where(
            JournalEntryLine.id
            == source_journal_entry_line_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    source = source_result.scalar_one()

    account_result = await db.execute(
        select(Account).where(
            Account.company_id == company_id,
            Account.id == source.account_id,
        )
    )

    account = account_result.scalar_one_or_none()

    if account is None:
        raise FixedAssetAcquisitionError(
            "source account not found in this company"
        )

    return journal, source, account


async def _active_total_for_asset(
    db: AsyncSession,
    *,
    company_id: int,
    fixed_asset_id: int,
) -> Decimal:
    value = await db.scalar(
        select(
            func.coalesce(
                func.sum(FixedAssetAcquisitionCost.amount),
                0,
            )
        ).where(
            FixedAssetAcquisitionCost.company_id
            == company_id,
            FixedAssetAcquisitionCost.fixed_asset_id
            == fixed_asset_id,
            FixedAssetAcquisitionCost.reversal_of_id.is_(None),
            _active_original_condition(),
        )
    )

    return Decimal(value or 0).quantize(Decimal("0.01"))


async def _sync_asset_original_cost(
    db: AsyncSession,
    asset: FixedAsset,
) -> None:
    asset.original_cost = await _active_total_for_asset(
        db,
        company_id=asset.company_id,
        fixed_asset_id=asset.id,
    )


async def list_fixed_asset_acquisition_costs(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
):
    await _require_asset(
        db,
        company_id,
        fixed_asset_id,
    )

    result = await db.execute(
        select(FixedAssetAcquisitionCost)
        .where(
            FixedAssetAcquisitionCost.company_id
            == company_id,
            FixedAssetAcquisitionCost.fixed_asset_id
            == fixed_asset_id,
        )
        .order_by(FixedAssetAcquisitionCost.id)
    )

    return list(result.scalars().all())


async def add_fixed_asset_acquisition_cost(
    db: AsyncSession,
    *,
    company_id: int,
    fixed_asset_id: int,
    request_key: str,
    cost_type: FixedAssetAcquisitionCostType,
    recognition_date: date,
    amount: Decimal,
    source_journal_entry_line_id: int,
    source_description: str | None,
    created_by: int,
) -> FixedAssetAcquisitionCost:
    request_key = _request_key(request_key)
    amount = _amount(amount)

    await _lock_company(db, company_id)

    previous_result = await db.execute(
        select(FixedAssetAcquisitionCost).where(
            FixedAssetAcquisitionCost.company_id
            == company_id,
            FixedAssetAcquisitionCost.request_key
            == request_key,
        )
    )

    previous = previous_result.scalar_one_or_none()

    if previous is not None:
        same_request = (
            previous.fixed_asset_id == fixed_asset_id
            and previous.cost_type == cost_type
            and previous.recognition_date == recognition_date
            and previous.amount == amount
            and previous.source_journal_entry_line_id
            == source_journal_entry_line_id
            and previous.source_description
            == source_description
            and previous.created_by == created_by
            and previous.reversal_of_id is None
        )

        if not same_request:
            raise FixedAssetAcquisitionError(
                "request_key already belongs to a different request"
            )

        return previous

    asset = await _require_asset(
        db,
        company_id,
        fixed_asset_id,
        for_update=True,
    )

    journal, source, account = await _load_source(
        db,
        company_id,
        source_journal_entry_line_id,
    )

    if journal.status != JournalEntryStatus.POSTED:
        raise FixedAssetAcquisitionError(
            "source journal entry must be posted"
        )

    if journal.reversal_of_id is not None:
        raise FixedAssetAcquisitionError(
            "reversal journal cannot be used as an acquisition source"
        )

    if journal.entry_date > recognition_date:
        raise FixedAssetAcquisitionError(
            "source journal date cannot be after recognition date"
        )

    if (
        source.credit != 0
        or not source.debit.is_finite()
        or source.debit <= 0
    ):
        raise FixedAssetAcquisitionError(
            "source must be a positive debit accounting line"
        )

    if not account.is_active or not account.is_postable:
        raise FixedAssetAcquisitionError(
            "source account must be active and postable"
        )

    await ensure_period_open(
        company_id=company_id,
        operation_date=recognition_date,
        db=db,
    )

    used = await db.scalar(
        select(
            func.coalesce(
                func.sum(FixedAssetAcquisitionCost.amount),
                0,
            )
        ).where(
            FixedAssetAcquisitionCost.company_id
            == company_id,
            FixedAssetAcquisitionCost.source_journal_entry_line_id
            == source.id,
            FixedAssetAcquisitionCost.reversal_of_id.is_(None),
            _active_original_condition(),
        )
    )

    used = Decimal(used or 0)

    if amount > source.debit - used:
        raise FixedAssetAcquisitionError(
            "amount exceeds unallocated source debit"
        )

    row = FixedAssetAcquisitionCost(
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        request_key=request_key,
        cost_type=cost_type,
        recognition_date=recognition_date,
        amount=amount,
        source_journal_entry_line_id=source.id,
        source_description=source_description,
        created_by=created_by,
    )

    db.add(row)
    await db.flush()

    await _sync_asset_original_cost(db, asset)
    await db.flush()

    return row


async def reverse_fixed_asset_acquisition_cost(
    db: AsyncSession,
    *,
    company_id: int,
    fixed_asset_id: int,
    acquisition_cost_id: int,
    reversal_date: date,
    reversed_by: int,
) -> FixedAssetAcquisitionCost:
    await _lock_company(db, company_id)

    asset = await _require_asset(
        db,
        company_id,
        fixed_asset_id,
        for_update=True,
    )

    result = await db.execute(
        select(FixedAssetAcquisitionCost)
        .where(
            FixedAssetAcquisitionCost.id
            == acquisition_cost_id,
            FixedAssetAcquisitionCost.company_id
            == company_id,
            FixedAssetAcquisitionCost.fixed_asset_id
            == fixed_asset_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    original = result.scalar_one_or_none()

    if original is None:
        raise FixedAssetAcquisitionNotFoundError(
            "fixed asset acquisition cost not found"
        )

    if original.reversal_of_id is not None:
        raise FixedAssetAcquisitionError(
            "a reversal acquisition cost cannot be reversed"
        )

    existing_reversal = await db.scalar(
        select(FixedAssetAcquisitionCost.id).where(
            FixedAssetAcquisitionCost.company_id
            == company_id,
            FixedAssetAcquisitionCost.reversal_of_id
            == original.id,
        )
    )

    if existing_reversal is not None:
        raise FixedAssetAcquisitionError(
            "fixed asset acquisition cost has already been reversed"
        )

    if reversal_date < original.recognition_date:
        raise FixedAssetAcquisitionError(
            "reversal date cannot precede recognition date"
        )

    await ensure_period_open(
        company_id=company_id,
        operation_date=reversal_date,
        db=db,
    )

    reversal = FixedAssetAcquisitionCost(
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        request_key=f"reversal:{original.id}",
        cost_type=original.cost_type,
        recognition_date=reversal_date,
        amount=original.amount,
        source_journal_entry_line_id=(
            original.source_journal_entry_line_id
        ),
        source_description=original.source_description,
        reversal_of_id=original.id,
        created_by=reversed_by,
    )

    db.add(reversal)
    await db.flush()

    await _sync_asset_original_cost(db, asset)
    await db.flush()

    return reversal
