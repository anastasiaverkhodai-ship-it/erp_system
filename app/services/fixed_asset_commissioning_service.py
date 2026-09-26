from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.account import Account

from app.models.fixed_asset import FixedAsset, FixedAssetStatus
from app.models.fixed_asset_acquisition import FixedAssetAcquisitionCost
from app.models.fixed_asset_commissioning import FixedAssetCommissioning
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.services.accounting_period_service import ensure_period_open
from app.services.accounting_posting import post_journal_entry
from app.services.fixed_asset_acquisition_service import _lock_company
from app.services.fixed_asset_service import _require_account, _require_group, _require_location, _require_responsible_person
from app.services.accounting_reversal import reverse_journal_entry


class FixedAssetCommissioningError(Exception):
    pass


class FixedAssetCommissioningNotFoundError(
    FixedAssetCommissioningError
):
    pass


def _active_acquisition_condition():
    reversal = aliased(FixedAssetAcquisitionCost)
    return ~select(reversal.id).where(
        reversal.company_id
        == FixedAssetAcquisitionCost.company_id,
        reversal.reversal_of_id
        == FixedAssetAcquisitionCost.id,
    ).exists()


async def _load_asset_for_update(
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
        raise FixedAssetCommissioningNotFoundError(
            "fixed asset not found"
        )
    return asset


async def _load_active_costs(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
) -> list[FixedAssetAcquisitionCost]:
    result = await db.execute(
        select(FixedAssetAcquisitionCost)
        .where(
            FixedAssetAcquisitionCost.company_id == company_id,
            FixedAssetAcquisitionCost.fixed_asset_id
            == fixed_asset_id,
            FixedAssetAcquisitionCost.reversal_of_id.is_(None),
            _active_acquisition_condition(),
        )
        .order_by(
            FixedAssetAcquisitionCost.id.asc()
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


async def _existing_by_request_key(
    db: AsyncSession,
    company_id: int,
    request_key: str,
) -> FixedAssetCommissioning | None:
    result = await db.execute(
        select(FixedAssetCommissioning).where(
            FixedAssetCommissioning.company_id == company_id,
            FixedAssetCommissioning.request_key == request_key,
        )
    )
    return result.scalar_one_or_none()


async def _active_commissioning(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
) -> FixedAssetCommissioning | None:
    reversal = aliased(FixedAssetCommissioning)

    result = await db.execute(
        select(FixedAssetCommissioning)
        .where(
            FixedAssetCommissioning.company_id == company_id,
            FixedAssetCommissioning.fixed_asset_id
            == fixed_asset_id,
            FixedAssetCommissioning.reversal_of_id.is_(None),
            ~select(reversal.id).where(
                reversal.company_id
                == FixedAssetCommissioning.company_id,
                reversal.reversal_of_id
                == FixedAssetCommissioning.id,
            ).exists(),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


def _validate_asset_ready(asset: FixedAsset) -> None:
    if asset.status != FixedAssetStatus.READY_FOR_COMMISSIONING:
        raise FixedAssetCommissioningError(
            "fixed asset must be ready for commissioning"
        )

    original_cost = Decimal(asset.original_cost or 0)
    salvage_value = Decimal(asset.salvage_value or 0)

    if not original_cost.is_finite() or not salvage_value.is_finite():
        raise FixedAssetCommissioningError("asset monetary values must be finite")

    if original_cost <= 0:
        raise FixedAssetCommissioningError(
            "fixed asset original cost must be positive"
        )

    if salvage_value < 0:
        raise FixedAssetCommissioningError(
            "fixed asset salvage value cannot be negative"
        )

    if salvage_value > original_cost:
        raise FixedAssetCommissioningError(
            "fixed asset salvage value cannot exceed original cost"
        )

    if not asset.useful_life_months or asset.useful_life_months <= 0:
        raise FixedAssetCommissioningError(
            "fixed asset useful life must be positive"
        )

    if asset.depreciation_method is None:
        raise FixedAssetCommissioningError(
            "fixed asset depreciation method is required"
        )

    if asset.asset_account_id is None:
        raise FixedAssetCommissioningError(
            "fixed asset account is required"
        )

    if asset.accumulated_depreciation_account_id is None:
        raise FixedAssetCommissioningError(
            "accumulated depreciation account is required"
        )

    if asset.depreciation_expense_account_id is None:
        raise FixedAssetCommissioningError(
            "depreciation expense account is required"
        )


async def create_and_post_commissioning(
    db: AsyncSession,
    *,
    company_id: int,
    fixed_asset_id: int,
    request_key: str,
    commissioning_date: date,
    created_by: int,
) -> FixedAssetCommissioning:
    request_key = request_key.strip()
    if not request_key or len(request_key) > 100 or request_key.startswith("reversal:"):
        raise FixedAssetCommissioningError(
            "request key is required"
        )

    await _lock_company(db, company_id)
    existing = await _existing_by_request_key(
        db,
        company_id,
        request_key,
    )
    if existing is not None:
        same_request = (
            existing.fixed_asset_id == fixed_asset_id
            and existing.commissioning_date == commissioning_date
            and existing.reversal_of_id is None
            and existing.created_by == created_by
        )

        if not same_request:
            raise FixedAssetCommissioningError(
                "request_key already belongs to a different request"
            )

        return existing

    asset = await _load_asset_for_update(
        db,
        company_id,
        fixed_asset_id,
    )

    _validate_asset_ready(asset)
    if commissioning_date > date.today():
        raise FixedAssetCommissioningError("commissioning date cannot be in the future")
    latest = await db.scalar(select(func.max(FixedAssetCommissioning.commissioning_date)).where(
        FixedAssetCommissioning.company_id == company_id,
        FixedAssetCommissioning.fixed_asset_id == fixed_asset_id))
    if latest is not None and commissioning_date < latest:
        raise FixedAssetCommissioningError("commissioning date precedes the latest lifecycle event")
    for account_id in (asset.asset_account_id, asset.accumulated_depreciation_account_id,
                       asset.depreciation_expense_account_id):
        await _require_account(db, company_id, account_id)
    await _require_group(db, company_id, asset.asset_group_id)
    await _require_location(db, company_id, asset.location_id)
    await _require_responsible_person(db, company_id, asset.responsible_person_id)

    if commissioning_date < asset.acquisition_date:
        raise FixedAssetCommissioningError(
            "commissioning date cannot precede acquisition date"
        )

    if await _active_commissioning(
        db,
        company_id,
        fixed_asset_id,
    ) is not None:
        raise FixedAssetCommissioningError(
            "fixed asset is already commissioned"
        )

    await ensure_period_open(
        company_id=company_id,
        operation_date=commissioning_date,
        db=db,
    )

    costs = await _load_active_costs(
        db,
        company_id,
        fixed_asset_id,
    )

    if not costs:
        raise FixedAssetCommissioningError(
            "fixed asset has no active acquisition costs"
        )

    total = sum(
        (Decimal(cost.amount) for cost in costs),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))

    original_cost = Decimal(
        asset.original_cost or 0
    ).quantize(Decimal("0.01"))

    if total != original_cost:
        raise FixedAssetCommissioningError(
            "active acquisition costs do not match fixed asset original cost"
        )

    if any(
        cost.recognition_date > commissioning_date
        for cost in costs
    ):
        raise FixedAssetCommissioningError(
            "commissioning date cannot precede acquisition recognition"
        )

    commissioning = FixedAssetCommissioning(
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        request_key=request_key,
        commissioning_date=commissioning_date,
        created_by=created_by,
    )
    db.add(commissioning)
    await db.flush()

    source_line_ids = [
        cost.source_journal_entry_line_id
        for cost in costs
    ]

    source_result = await db.execute(
        select(
            JournalEntryLine,
            JournalEntry,
            Account,
        )
        .join(
            JournalEntry,
            JournalEntry.id
            == JournalEntryLine.journal_entry_id,
        )
        .join(
            Account,
            Account.id == JournalEntryLine.account_id,
        )
        .where(
            JournalEntryLine.id.in_(source_line_ids),
            JournalEntry.company_id == company_id,
            Account.company_id == company_id,
        )
        .with_for_update()
    )

    source_rows = {
        line.id: (line, journal, account)
        for line, journal, account
        in source_result.all()
    }

    if len(source_rows) != len(set(source_line_ids)):
        raise FixedAssetCommissioningError(
            "one or more acquisition sources are missing "
            "or belong to another company"
        )

    for line_id, (source_line, source_journal, _) in source_rows.items():
        used = await db.scalar(select(func.coalesce(func.sum(FixedAssetAcquisitionCost.amount), 0)).where(
            FixedAssetAcquisitionCost.company_id == company_id,
            FixedAssetAcquisitionCost.source_journal_entry_line_id == line_id,
            FixedAssetAcquisitionCost.reversal_of_id.is_(None), _active_acquisition_condition()))
        if not source_line.debit.is_finite() or used > source_line.debit:
            raise FixedAssetCommissioningError("acquisition allocations exceed the source debit")
        if any(cost.recognition_date < source_journal.entry_date for cost in costs
               if cost.source_journal_entry_line_id == line_id):
            raise FixedAssetCommissioningError("acquisition recognition precedes its source journal")

    credits: dict[int, Decimal] = {}

    for cost in costs:
        source_line, source_journal, source_account = source_rows[
            cost.source_journal_entry_line_id
        ]

        if source_journal.status != JournalEntryStatus.POSTED:
            raise FixedAssetCommissioningError(
                "acquisition source journal entry must still be posted"
            )

        if source_journal.reversal_of_id is not None:
            raise FixedAssetCommissioningError(
                "reversal journal cannot be an active acquisition source"
            )

        if (
            source_line.credit != 0
            or not source_line.debit.is_finite()
            or source_line.debit <= 0
        ):
            raise FixedAssetCommissioningError(
                "acquisition source must remain a positive debit line"
            )

        if (
            not source_account.is_active
            or not source_account.is_postable
        ):
            raise FixedAssetCommissioningError(
                "acquisition source account must remain active "
                "and postable"
            )

        if source_account.id == asset.asset_account_id:
            raise FixedAssetCommissioningError(
                "acquisition source account must differ "
                "from fixed asset account"
            )

        credits[source_line.account_id] = (
            credits.get(
                source_line.account_id,
                Decimal("0.00"),
            )
            + Decimal(cost.amount)
        )

    entry = JournalEntry(
        company_id=company_id,
        fixed_asset_commissioning_id=commissioning.id,
        entry_date=commissioning_date,
        description=(
            f"Fixed asset commissioning: "
            f"{asset.asset_number} - {asset.name}"
        ),
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )

    lines = [
        JournalEntryLine(
            line_no=1,
            account_id=asset.asset_account_id,
            debit=total,
            credit=Decimal("0.00"),
            description="Fixed asset commissioning",
        )
    ]

    line_no = 2
    for account_id, amount in sorted(credits.items()):
        lines.append(
            JournalEntryLine(
                line_no=line_no,
                account_id=account_id,
                debit=Decimal("0.00"),
                credit=amount.quantize(
                    Decimal("0.01")
                ),
                description=(
                    "Capital investment transferred "
                    "to fixed asset"
                ),
            )
        )
        line_no += 1

    entry.lines = lines
    db.add(entry)
    await db.flush()

    previous_context = db.info.get("fixed_asset_commissioning")
    db.info["fixed_asset_commissioning"] = commissioning.id
    try:
        await post_journal_entry(db, company_id, entry.id)
    finally:
        if previous_context is None:
            db.info.pop("fixed_asset_commissioning", None)
        else:
            db.info["fixed_asset_commissioning"] = previous_context

    asset.status = FixedAssetStatus.IN_SERVICE
    asset.in_service_date = commissioning_date

    await db.flush()
    return commissioning


async def reverse_commissioning(
    db: AsyncSession,
    *,
    company_id: int,
    fixed_asset_id: int,
    commissioning_id: int,
    reversal_date: date,
    reversed_by: int,
) -> FixedAssetCommissioning:
    await _lock_company(db, company_id)
    asset = await _load_asset_for_update(
        db,
        company_id,
        fixed_asset_id,
    )

    result = await db.execute(
        select(FixedAssetCommissioning)
        .where(
            FixedAssetCommissioning.id == commissioning_id,
            FixedAssetCommissioning.company_id == company_id,
            FixedAssetCommissioning.fixed_asset_id
            == fixed_asset_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    original = result.scalar_one_or_none()

    if original is None:
        raise FixedAssetCommissioningNotFoundError(
            "fixed asset commissioning not found"
        )

    if original.reversal_of_id is not None:
        raise FixedAssetCommissioningError(
            "a reversal commissioning cannot be reversed"
        )

    existing_reversal = await db.scalar(
        select(FixedAssetCommissioning).where(
            FixedAssetCommissioning.company_id == company_id,
            FixedAssetCommissioning.reversal_of_id
            == original.id,
        )
    )
    if existing_reversal is not None:
        if existing_reversal.commissioning_date != reversal_date:
            raise FixedAssetCommissioningError("commissioning reversed on another date")
        return existing_reversal

    if reversal_date > date.today():
        raise FixedAssetCommissioningError("reversal date cannot be in the future")
    if asset.status != FixedAssetStatus.IN_SERVICE:
        raise FixedAssetCommissioningError("only an in-service asset can reverse commissioning")
    if reversal_date < original.commissioning_date:
        raise FixedAssetCommissioningError(
            "reversal date cannot precede commissioning date"
        )

    await ensure_period_open(
        company_id=company_id,
        operation_date=reversal_date,
        db=db,
    )

    journal_result = await db.execute(
        select(JournalEntry)
        .where(
            JournalEntry.company_id == company_id,
            JournalEntry.fixed_asset_commissioning_id
            == original.id,
            JournalEntry.reversal_of_id.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    journal = journal_result.scalar_one_or_none()

    if journal is None:
        raise FixedAssetCommissioningError(
            "commissioning journal entry not found"
        )

    if journal.status != JournalEntryStatus.POSTED:
        raise FixedAssetCommissioningError(
            "commissioning journal entry is not posted"
        )

    reversal = FixedAssetCommissioning(
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        request_key=f"reversal:{original.id}",
        commissioning_date=reversal_date,
        reversal_of_id=original.id,
        created_by=reversed_by,
    )
    db.add(reversal)
    await db.flush()

    previous_context = db.info.get("fixed_asset_commissioning")
    db.info["fixed_asset_commissioning"] = original.id
    try:
        reversal_entry = await reverse_journal_entry(
            db=db, company_id=company_id, journal_entry_id=journal.id,
            reversal_date=reversal_date, reversed_by=reversed_by)
    finally:
        if previous_context is None:
            db.info.pop("fixed_asset_commissioning", None)
        else:
            db.info["fixed_asset_commissioning"] = previous_context

    reversal_entry.fixed_asset_commissioning_id = reversal.id

    asset.status = FixedAssetStatus.READY_FOR_COMMISSIONING
    asset.in_service_date = None

    await db.flush()
    return reversal
