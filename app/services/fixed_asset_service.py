from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.fixed_asset import (
    FixedAsset,
    FixedAssetCardHistory,
    FixedAssetGroup,
    FixedAssetLocation,
    FixedAssetResponsiblePerson,
)
from app.schemas.fixed_asset import (
    FixedAssetCreate,
    FixedAssetGroupCreate,
    FixedAssetGroupUpdate,
    FixedAssetLocationCreate,
    FixedAssetLocationUpdate,
    FixedAssetResponsiblePersonCreate,
    FixedAssetResponsiblePersonUpdate,
    FixedAssetUpdate,
)


class FixedAssetNotFoundError(ValueError):
    pass


class FixedAssetValidationError(ValueError):
    pass


async def _get_company_row(db, model, company_id: int, row_id: int):
    result = await db.execute(
        select(model).where(
            model.company_id == company_id,
            model.id == row_id,
        )
    )
    return result.scalar_one_or_none()


async def _require_group(db, company_id: int, group_id: int):
    row = await _get_company_row(db, FixedAssetGroup, company_id, group_id)
    if row is None:
        raise FixedAssetValidationError("fixed asset group not found for company")
    return row


async def _require_location(db, company_id: int, location_id: int | None):
    if location_id is None:
        return None
    row = await _get_company_row(db, FixedAssetLocation, company_id, location_id)
    if row is None:
        raise FixedAssetValidationError("fixed asset location not found for company")
    return row


async def _require_responsible_person(
    db,
    company_id: int,
    responsible_person_id: int | None,
):
    if responsible_person_id is None:
        return None
    row = await _get_company_row(
        db,
        FixedAssetResponsiblePerson,
        company_id,
        responsible_person_id,
    )
    if row is None:
        raise FixedAssetValidationError(
            "fixed asset responsible person not found for company"
        )
    return row


async def _require_account(db, company_id: int, account_id: int):
    result = await db.execute(
        select(Account).where(
            Account.company_id == company_id,
            Account.id == account_id,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise FixedAssetValidationError("account not found for company")
    if hasattr(account, "is_active") and not account.is_active:
        raise FixedAssetValidationError("account must be active")
    if hasattr(account, "is_postable") and not account.is_postable:
        raise FixedAssetValidationError("account must be postable")
    return account


async def list_fixed_asset_groups(db: AsyncSession, company_id: int):
    result = await db.execute(
        select(FixedAssetGroup)
        .where(FixedAssetGroup.company_id == company_id)
        .order_by(FixedAssetGroup.code, FixedAssetGroup.id)
    )
    return list(result.scalars().all())


async def create_fixed_asset_group(
    db: AsyncSession,
    company_id: int,
    data: FixedAssetGroupCreate,
):
    row = FixedAssetGroup(company_id=company_id, **data.model_dump())
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def update_fixed_asset_group(
    db: AsyncSession,
    company_id: int,
    group_id: int,
    data: FixedAssetGroupUpdate,
):
    row = await _get_company_row(db, FixedAssetGroup, company_id, group_id)
    if row is None:
        raise FixedAssetNotFoundError("fixed asset group not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    await db.flush()
    await db.refresh(row)
    return row


async def list_fixed_asset_locations(db: AsyncSession, company_id: int):
    result = await db.execute(
        select(FixedAssetLocation)
        .where(FixedAssetLocation.company_id == company_id)
        .order_by(FixedAssetLocation.code, FixedAssetLocation.id)
    )
    return list(result.scalars().all())


async def create_fixed_asset_location(
    db: AsyncSession,
    company_id: int,
    data: FixedAssetLocationCreate,
):
    row = FixedAssetLocation(company_id=company_id, **data.model_dump())
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def update_fixed_asset_location(
    db: AsyncSession,
    company_id: int,
    location_id: int,
    data: FixedAssetLocationUpdate,
):
    row = await _get_company_row(db, FixedAssetLocation, company_id, location_id)
    if row is None:
        raise FixedAssetNotFoundError("fixed asset location not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    await db.flush()
    await db.refresh(row)
    return row


async def list_fixed_asset_responsible_persons(
    db: AsyncSession,
    company_id: int,
):
    result = await db.execute(
        select(FixedAssetResponsiblePerson)
        .where(FixedAssetResponsiblePerson.company_id == company_id)
        .order_by(
            FixedAssetResponsiblePerson.display_name,
            FixedAssetResponsiblePerson.id,
        )
    )
    return list(result.scalars().all())


async def create_fixed_asset_responsible_person(
    db: AsyncSession,
    company_id: int,
    data: FixedAssetResponsiblePersonCreate,
):
    row = FixedAssetResponsiblePerson(
        company_id=company_id,
        **data.model_dump(),
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def update_fixed_asset_responsible_person(
    db: AsyncSession,
    company_id: int,
    responsible_person_id: int,
    data: FixedAssetResponsiblePersonUpdate,
):
    row = await _get_company_row(
        db,
        FixedAssetResponsiblePerson,
        company_id,
        responsible_person_id,
    )
    if row is None:
        raise FixedAssetNotFoundError("fixed asset responsible person not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    await db.flush()
    await db.refresh(row)
    return row


async def list_fixed_assets(db: AsyncSession, company_id: int):
    result = await db.execute(
        select(FixedAsset)
        .where(FixedAsset.company_id == company_id)
        .order_by(FixedAsset.asset_number, FixedAsset.id)
    )
    return list(result.scalars().all())


async def get_fixed_asset(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
):
    row = await _get_company_row(
        db,
        FixedAsset,
        company_id,
        fixed_asset_id,
    )
    if row is None:
        raise FixedAssetNotFoundError("fixed asset not found")
    return row


async def create_fixed_asset(
    db: AsyncSession,
    company_id: int,
    data: FixedAssetCreate,
    created_by: int,
):
    await _require_group(db, company_id, data.asset_group_id)
    await _require_location(db, company_id, data.location_id)
    await _require_responsible_person(
        db,
        company_id,
        data.responsible_person_id,
    )

    await _require_account(db, company_id, data.asset_account_id)
    await _require_account(
        db,
        company_id,
        data.accumulated_depreciation_account_id,
    )
    await _require_account(
        db,
        company_id,
        data.depreciation_expense_account_id,
    )

    row = FixedAsset(
        company_id=company_id,
        created_by=created_by,
        **data.model_dump(),
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def update_fixed_asset(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
    data: FixedAssetUpdate,
    changed_by: int,
):
    row = await get_fixed_asset(db, company_id, fixed_asset_id)
    changes = data.model_dump(exclude_unset=True)
    effective_date = changes.pop("effective_date")

    new_group_id = changes.get("asset_group_id", row.asset_group_id)
    new_location_id = changes.get("location_id", row.location_id)
    new_responsible_person_id = changes.get(
        "responsible_person_id",
        row.responsible_person_id,
    )

    await _require_group(db, company_id, new_group_id)
    await _require_location(db, company_id, new_location_id)
    await _require_responsible_person(
        db,
        company_id,
        new_responsible_person_id,
    )

    new_name = changes.get("name", row.name)
    new_useful_life = changes.get(
        "useful_life_months",
        row.useful_life_months,
    )
    new_salvage = changes.get("salvage_value", row.salvage_value)
    new_method = changes.get(
        "depreciation_method",
        row.depreciation_method,
    )

    if new_salvage > row.original_cost:
        raise FixedAssetValidationError(
            "salvage_value cannot exceed original_cost"
        )

    if "in_service_date" in changes:
        value = changes["in_service_date"]
        if value is not None and value < row.acquisition_date:
            raise FixedAssetValidationError(
                "in_service_date cannot precede acquisition_date"
            )

    card_changed = any(
        key in changes
        for key in (
            "name",
            "useful_life_months",
            "salvage_value",
            "depreciation_method",
            "asset_group_id",
            "location_id",
            "responsible_person_id",
        )
    )

    for key, value in changes.items():
        setattr(row, key, value)

    if card_changed:
        history = FixedAssetCardHistory(
            company_id=company_id,
            fixed_asset_id=row.id,
            effective_date=effective_date,
            asset_group_id=new_group_id,
            location_id=new_location_id,
            responsible_person_id=new_responsible_person_id,
            name=new_name,
            useful_life_months=new_useful_life,
            salvage_value=new_salvage,
            depreciation_method=new_method,
            changed_by=changed_by,
        )
        db.add(history)

    await db.flush()
    await db.refresh(row)
    return row


async def list_fixed_asset_card_history(
    db: AsyncSession,
    company_id: int,
    fixed_asset_id: int,
):
    await get_fixed_asset(db, company_id, fixed_asset_id)
    result = await db.execute(
        select(FixedAssetCardHistory)
        .where(
            FixedAssetCardHistory.company_id == company_id,
            FixedAssetCardHistory.fixed_asset_id == fixed_asset_id,
        )
        .order_by(
            FixedAssetCardHistory.effective_date,
            FixedAssetCardHistory.id,
        )
    )
    return list(result.scalars().all())
