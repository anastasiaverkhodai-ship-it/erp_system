from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.permissions import require_company_permission
from app.models.user import User
from app.schemas.fixed_asset_repair_improvement import (
    FixedAssetRepairImprovementCreate,
    FixedAssetRepairImprovementResponse,
    FixedAssetRepairImprovementReverse,
)
from app.services.fixed_asset_repair_improvement_service import (
    FixedAssetRepairImprovementError,
    create_fixed_asset_repair_improvement,
    list_fixed_asset_repair_improvements,
    reverse_fixed_asset_repair_improvement,
)

router = APIRouter(
    prefix="/companies/{company_id}/fixed-assets/{fixed_asset_id}/repair-improvements",
    tags=["fixed-assets"],
)


@router.get(
    "",
    response_model=list[FixedAssetRepairImprovementResponse],
)
async def get_repair_improvements(
    company_id: int,
    fixed_asset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission("journal_entries.read")
    ),
):
    return await list_fixed_asset_repair_improvements(
        db,
        company_id,
        fixed_asset_id,
    )


@router.post(
    "",
    response_model=FixedAssetRepairImprovementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_repair_improvement(
    company_id: int,
    fixed_asset_id: int,
    payload: FixedAssetRepairImprovementCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission("journal_entries.create")
    ),
):
    try:
        row = await create_fixed_asset_repair_improvement(
            db,
            company_id,
            fixed_asset_id,
            payload,
            current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except FixedAssetRepairImprovementError as exc:
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
    response_model=FixedAssetRepairImprovementResponse,
)
async def post_repair_improvement_reversal(
    company_id: int,
    fixed_asset_id: int,
    operation_id: int,
    payload: FixedAssetRepairImprovementReverse,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission("journal_entries.create")
    ),
):
    try:
        row = await reverse_fixed_asset_repair_improvement(
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
    except FixedAssetRepairImprovementError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise
