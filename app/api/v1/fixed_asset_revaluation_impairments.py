from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.fixed_asset_revaluation_impairment import (
    FixedAssetRevaluationImpairmentCreate,
    FixedAssetRevaluationImpairmentResponse,
    FixedAssetRevaluationImpairmentReverse,
)
from app.services.fixed_asset_revaluation_impairment_service import (
    FixedAssetRevaluationImpairmentError,
    create_fixed_asset_revaluation_impairment,
    list_fixed_asset_revaluation_impairments,
    reverse_fixed_asset_revaluation_impairment,
)


router = APIRouter(
    prefix=(
        "/companies/{company_id}/fixed-assets/"
        "{fixed_asset_id}/revaluation-impairments"
    ),
    tags=["fixed-assets"],
)


@router.get(
    "",
    response_model=list[FixedAssetRevaluationImpairmentResponse],
)
async def get_revaluation_impairments(
    company_id: int,
    fixed_asset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission("journal_entries.read")
    ),
):
    return await list_fixed_asset_revaluation_impairments(
        db,
        company_id,
        fixed_asset_id,
    )


@router.post(
    "",
    response_model=FixedAssetRevaluationImpairmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_revaluation_impairment(
    company_id: int,
    fixed_asset_id: int,
    payload: FixedAssetRevaluationImpairmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission("journal_entries.create")
    ),
):
    try:
        row = await create_fixed_asset_revaluation_impairment(
            db,
            company_id,
            fixed_asset_id,
            payload,
            current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except FixedAssetRevaluationImpairmentError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.post(
    "/{operation_id}/reverse",
    response_model=FixedAssetRevaluationImpairmentResponse,
)
async def post_revaluation_impairment_reversal(
    company_id: int,
    fixed_asset_id: int,
    operation_id: int,
    payload: FixedAssetRevaluationImpairmentReverse,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission("journal_entries.create")
    ),
):
    try:
        row = await reverse_fixed_asset_revaluation_impairment(
            db,
            company_id,
            fixed_asset_id,
            operation_id,
            payload.reversal_date,
            payload.request_key,
            current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except FixedAssetRevaluationImpairmentError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise
