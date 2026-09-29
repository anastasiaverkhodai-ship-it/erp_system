"""Shared ordering and serialization for fixed-asset operations."""
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.models.fixed_asset import FixedAssetCardHistory
from app.models.fixed_asset_commissioning import FixedAssetCommissioning
from app.models.fixed_asset_depreciation import FixedAssetDepreciation
from app.models.fixed_asset_disposal import FixedAssetDisposal
from app.models.fixed_asset_repair_improvement import FixedAssetRepairImprovement
from app.models.fixed_asset_revaluation_impairment import FixedAssetRevaluationImpairment

EVENTS = (
    (FixedAssetCommissioning, 'commissioning_date'),
    (FixedAssetDepreciation, 'posting_date'),
    (FixedAssetDisposal, 'disposal_date'),
    (FixedAssetRepairImprovement, 'operation_date'),
    (FixedAssetRevaluationImpairment, 'operation_date'),
)


def event_time():
    # now() in PostgreSQL is transaction time: several events in one transaction
    # need distinct timestamps to preserve their actual creation order.
    return datetime.now(timezone.utc)


async def lock_company(db, company_id):
    from app.services.fixed_asset_acquisition_service import _lock_company
    await _lock_company(db, company_id)


async def require_chronology(db, asset, operation_date):
    if operation_date > date.today():
        raise HTTPException(409, 'Fixed asset operation cannot be in the future')
    earliest = asset.in_service_date or asset.acquisition_date
    if operation_date < earliest:
        raise HTTPException(409, 'Fixed asset operation precedes acquisition/in-service date')
    for model, field in EVENTS + ((FixedAssetCardHistory, 'effective_date'),):
        later = await db.scalar(select(model.id).where(
            model.company_id == asset.company_id, model.fixed_asset_id == asset.id,
            getattr(model, field) > operation_date).limit(1))
        if later is not None:
            raise HTTPException(409, 'Fixed asset operation precedes later lifecycle history')
    from app.models.fixed_asset_opening_balance import FixedAssetOpeningBalance
    from app.models.opening_balance import OpeningBalance
    opening_date = await db.scalar(select(OpeningBalance.opening_date).join(
        FixedAssetOpeningBalance, FixedAssetOpeningBalance.opening_balance_id == OpeningBalance.id
    ).where(FixedAssetOpeningBalance.company_id == asset.company_id,
            FixedAssetOpeningBalance.fixed_asset_id == asset.id))
    if opening_date is not None and operation_date < opening_date:
        raise HTTPException(409, 'Fixed asset operation precedes opening cutover')


async def require_no_later_events(db, company_id, asset_id, original):
    """Reverse dependent active events first, including events on the same day."""
    for model, _ in EVENTS:
        threshold = original.created_at
        if model.__table__.c.created_at.type.timezone:
            if threshold.tzinfo is None:
                threshold = threshold.replace(tzinfo=timezone.utc)
        else:
            threshold = threshold.replace(tzinfo=None)
        reversal = aliased(model)
        stmt = select(model.id).where(
            model.company_id == company_id, model.fixed_asset_id == asset_id,
            model.reversal_of_id.is_(None),
            ~select(reversal.id).where(reversal.company_id == company_id,
                                      reversal.reversal_of_id == model.id).exists(),
            model.created_at >= threshold)
        if isinstance(original, model):
            stmt = stmt.where(model.id != original.id)
        if await db.scalar(stmt.limit(1)) is not None:
            raise HTTPException(409, 'Reverse later fixed asset lifecycle events first')
