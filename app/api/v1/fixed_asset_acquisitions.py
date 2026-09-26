from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.fixed_asset_acquisition import (
    FixedAssetAcquisitionCostCreate,
    FixedAssetAcquisitionCostRead,
    FixedAssetAcquisitionCostReverse,
)
from app.services.fixed_asset_acquisition_service import (
    FixedAssetAcquisitionError,
    FixedAssetAcquisitionNotFoundError,
    add_fixed_asset_acquisition_cost,
    list_fixed_asset_acquisition_costs,
    reverse_fixed_asset_acquisition_cost,
)


router = APIRouter(
    prefix="/companies/{company_id}/fixed-assets/{fixed_asset_id}/acquisition-costs",
    tags=["fixed-assets"],
)


def _raise_service_error(exc: Exception):
    if isinstance(exc, FixedAssetAcquisitionNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc))
    raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "",
    response_model=list[FixedAssetAcquisitionCostRead],
)
async def get_acquisition_costs(
    company_id: int,
    fixed_asset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await list_fixed_asset_acquisition_costs(
            db,
            company_id,
            fixed_asset_id,
        )
    except FixedAssetAcquisitionError as exc:
        _raise_service_error(exc)


@router.post(
    "",
    response_model=FixedAssetAcquisitionCostRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_acquisition_cost(
    company_id: int,
    fixed_asset_id: int,
    payload: FixedAssetAcquisitionCostCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = await add_fixed_asset_acquisition_cost(
            db,
            company_id=company_id,
            fixed_asset_id=fixed_asset_id,
            request_key=payload.request_key,
            cost_type=payload.cost_type,
            recognition_date=payload.recognition_date,
            amount=payload.amount,
            source_journal_entry_line_id=payload.source_journal_entry_line_id,
            source_description=payload.source_description,
            created_by=current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except FixedAssetAcquisitionError as exc:
        await db.rollback()
        _raise_service_error(exc)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="fixed asset acquisition conflict",
        )
    except Exception:
        await db.rollback()
        raise


@router.post(
    "/{acquisition_cost_id}/reverse",
    response_model=FixedAssetAcquisitionCostRead,
)
async def post_acquisition_cost_reversal(
    company_id: int,
    fixed_asset_id: int,
    acquisition_cost_id: int,
    payload: FixedAssetAcquisitionCostReverse,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = await reverse_fixed_asset_acquisition_cost(
            db,
            company_id=company_id,
            acquisition_cost_id=acquisition_cost_id,
            reversal_date=payload.reversal_date,
            reversed_by=current_user.id,
        )

        if row.fixed_asset_id != fixed_asset_id:
            await db.rollback()
            raise HTTPException(
                status_code=404,
                detail="fixed asset acquisition cost not found",
            )

        await db.commit()
        await db.refresh(row)
        return row
    except FixedAssetAcquisitionError as exc:
        await db.rollback()
        _raise_service_error(exc)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="fixed asset acquisition reversal conflict",
        )
    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        raise
