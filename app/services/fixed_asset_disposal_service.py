from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.fixed_asset import (
    FixedAsset,
    FixedAssetStatus,
)
from app.models.fixed_asset_disposal import (
    FixedAssetDisposal,
)
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import (
    JournalEntryLine,
)
from app.schemas.fixed_asset_disposal import (
    FixedAssetDisposalCreate,
)
from app.services.accounting_period_service import (
    ensure_period_open,
)
from app.services.accounting_posting import (
    post_journal_entry,
)
from app.services.accounting_reversal import (
    reverse_journal_entry,
)
from app.services.fixed_asset_revaluation_impairment_service import (
    _accumulated,
)


class FixedAssetDisposalError(Exception):
    pass


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(
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
    )

    if asset is None:
        raise FixedAssetDisposalError(
            "fixed asset not found for company"
        )

    return asset


async def _get_disposal_account(
    db: AsyncSession,
    company_id: int,
    account_id: int,
) -> Account:
    account = await db.scalar(
        select(Account).where(
            Account.company_id == company_id,
            Account.id == account_id,
        )
    )

    if account is None:
        raise FixedAssetDisposalError(
            "disposal account not found"
        )

    if not account.is_active or not account.is_postable:
        raise FixedAssetDisposalError(
            "disposal account must be active and postable"
        )

    return account


async def list_fixed_asset_disposals(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
) -> list[FixedAssetDisposal]:
    asset = await db.scalar(
        select(FixedAsset.id).where(
            FixedAsset.company_id == company_id,
            FixedAsset.id == fixed_asset_id,
        )
    )

    if asset is None:
        raise FixedAssetDisposalError(
            "fixed asset not found for company"
        )

    result = await db.scalars(
        select(FixedAssetDisposal)
        .where(
            FixedAssetDisposal.company_id == company_id,
            FixedAssetDisposal.fixed_asset_id == fixed_asset_id,
        )
        .order_by(
            FixedAssetDisposal.disposal_date,
            FixedAssetDisposal.id,
        )
    )

    return list(result.all())


async def create_fixed_asset_disposal(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
    data: FixedAssetDisposalCreate,
    created_by: int,
) -> FixedAssetDisposal:
    await ensure_period_open(
        company_id=company_id,
        operation_date=data.disposal_date,
        db=db,
    )

    asset = await _get_asset_for_update(
        db,
        company_id,
        fixed_asset_id,
    )

    existing = await db.scalar(
        select(FixedAssetDisposal).where(
            FixedAssetDisposal.company_id == company_id,
            FixedAssetDisposal.request_key == data.request_key,
        )
    )

    if existing is not None:
        same = (
            existing.fixed_asset_id == fixed_asset_id
            and existing.disposal_date == data.disposal_date
            and existing.disposal_account_id
            == data.disposal_account_id
            and (existing.description or None)
            == (data.description or None)
            and existing.reversal_of_id is None
        )

        if not same:
            raise FixedAssetDisposalError(
                "request key already used with different payload"
            )

        return existing

    asset_status = getattr(
        asset.status,
        "value",
        asset.status,
    )

    if asset_status == FixedAssetStatus.DISPOSED.value:
        raise FixedAssetDisposalError(
            "fixed asset is already disposed"
        )

    if asset_status not in {
        FixedAssetStatus.IN_SERVICE.value,
        FixedAssetStatus.SUSPENDED.value,
    }:
        raise FixedAssetDisposalError(
            "only in-service or suspended fixed asset "
            "can be disposed"
        )

    await _get_disposal_account(
        db,
        company_id,
        data.disposal_account_id,
    )

    accumulated = _money(
        await _accumulated(
            db,
            company_id,
            fixed_asset_id,
        )
    )

    original_cost = _money(
        asset.original_cost
    )
    salvage_value = _money(
        asset.salvage_value
    )

    if accumulated > original_cost:
        raise FixedAssetDisposalError(
            "accumulated depreciation exceeds original cost"
        )

    carrying_amount = _money(
        original_cost - accumulated
    )

    row = FixedAssetDisposal(
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        disposal_date=data.disposal_date,
        original_cost=original_cost,
        accumulated_depreciation=accumulated,
        carrying_amount=carrying_amount,
        salvage_value=salvage_value,
        previous_status=asset_status,
        disposal_account_id=data.disposal_account_id,
        request_key=data.request_key,
        description=data.description,
        created_by=created_by,
    )

    db.add(row)
    await db.flush()

    journal = JournalEntry(
        company_id=company_id,
        fixed_asset_disposal_id=row.id,
        entry_date=data.disposal_date,
        description=(
            data.description
            or f"Fixed asset disposal {asset.asset_number}"
        ),
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )

    db.add(journal)
    await db.flush()

    lines = []
    line_no = 1

    if accumulated > 0:
        lines.append(
            JournalEntryLine(
                line_no=line_no,
                journal_entry_id=journal.id,
                account_id=(
                    asset.accumulated_depreciation_account_id
                ),
                debit=accumulated,
                credit=Decimal("0.00"),
            )
        )
        line_no += 1

    if carrying_amount > 0:
        lines.append(
            JournalEntryLine(
                line_no=line_no,
                journal_entry_id=journal.id,
                account_id=data.disposal_account_id,
                debit=carrying_amount,
                credit=Decimal("0.00"),
            )
        )
        line_no += 1

    lines.append(
        JournalEntryLine(
            line_no=line_no,
            journal_entry_id=journal.id,
            account_id=asset.asset_account_id,
            debit=Decimal("0.00"),
            credit=original_cost,
        )
    )

    total_debit = sum(
        (
            Decimal(line.debit or 0)
            for line in lines
        ),
        Decimal("0.00"),
    )
    total_credit = sum(
        (
            Decimal(line.credit or 0)
            for line in lines
        ),
        Decimal("0.00"),
    )

    if _money(total_debit) != _money(total_credit):
        raise FixedAssetDisposalError(
            "disposal journal is not balanced"
        )

    db.add_all(lines)
    await db.flush()

    previous = db.info.get(
        "fixed_asset_disposal"
    )
    db.info["fixed_asset_disposal"] = row.id

    try:
        await post_journal_entry(
            db,
            company_id,
            journal.id,
        )
    finally:
        if previous is None:
            db.info.pop(
                "fixed_asset_disposal",
                None,
            )
        else:
            db.info[
                "fixed_asset_disposal"
            ] = previous

    row.journal_entry_id = journal.id
    asset.status = FixedAssetStatus.DISPOSED

    await db.flush()

    return row


async def reverse_fixed_asset_disposal(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
    operation_id: int,
    reversal_date: date,
    request_key: str,
    reversed_by: int,
) -> FixedAssetDisposal:
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

    original = await db.scalar(
        select(FixedAssetDisposal)
        .where(
            FixedAssetDisposal.company_id == company_id,
            FixedAssetDisposal.id == operation_id,
            FixedAssetDisposal.fixed_asset_id
            == fixed_asset_id,
        )
        .with_for_update()
    )

    if original is None:
        raise FixedAssetDisposalError(
            "fixed asset disposal not found"
        )

    if original.reversal_of_id is not None:
        raise FixedAssetDisposalError(
            "cannot reverse a reversal"
        )

    existing = await db.scalar(
        select(FixedAssetDisposal).where(
            FixedAssetDisposal.company_id == company_id,
            FixedAssetDisposal.request_key == request_key,
        )
    )

    if existing is not None:
        if existing.reversal_of_id != original.id:
            raise FixedAssetDisposalError(
                "request key already used with different payload"
            )
        return existing

    prior_reversal = await db.scalar(
        select(FixedAssetDisposal).where(
            FixedAssetDisposal.company_id == company_id,
            FixedAssetDisposal.reversal_of_id == original.id,
        )
    )

    if prior_reversal is not None:
        return prior_reversal

    current_status = getattr(
        asset.status,
        "value",
        asset.status,
    )

    if current_status != FixedAssetStatus.DISPOSED.value:
        raise FixedAssetDisposalError(
            "asset is no longer in deterministic disposed state"
        )

    if original.journal_entry_id is None:
        raise FixedAssetDisposalError(
            "disposal has no journal entry"
        )

    reversal = FixedAssetDisposal(
        company_id=company_id,
        fixed_asset_id=fixed_asset_id,
        disposal_date=reversal_date,
        original_cost=original.original_cost,
        accumulated_depreciation=(
            original.accumulated_depreciation
        ),
        carrying_amount=original.carrying_amount,
        salvage_value=original.salvage_value,
        previous_status=original.previous_status,
        disposal_account_id=original.disposal_account_id,
        request_key=request_key,
        description=(
            f"Reversal of fixed asset disposal {original.id}"
        ),
        reversal_of_id=original.id,
        created_by=reversed_by,
    )

    db.add(reversal)
    await db.flush()

    previous = db.info.get(
        "fixed_asset_disposal"
    )
    db.info["fixed_asset_disposal"] = original.id

    try:
        reversal_journal = await reverse_journal_entry(
            db=db,
            company_id=company_id,
            journal_entry_id=original.journal_entry_id,
            reversal_date=reversal_date,
            reversed_by=reversed_by,
        )
    finally:
        if previous is None:
            db.info.pop(
                "fixed_asset_disposal",
                None,
            )
        else:
            db.info[
                "fixed_asset_disposal"
            ] = previous

    reversal_journal.fixed_asset_disposal_id = (
        reversal.id
    )
    reversal.journal_entry_id = reversal_journal.id

    try:
        asset.status = FixedAssetStatus(
            original.previous_status
        )
    except ValueError as exc:
        raise FixedAssetDisposalError(
            "invalid previous fixed asset status"
        ) from exc

    await db.flush()

    return reversal
