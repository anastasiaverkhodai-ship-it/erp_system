from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.schemas.fixed_asset_disposal import (
    FixedAssetDisposalCreate,
    FixedAssetDisposalRead,
    FixedAssetDisposalReverse,
)
from app.services.fixed_asset_disposal_service import (
    FixedAssetDisposalError,
    create_fixed_asset_disposal,
    list_fixed_asset_disposals,
    reverse_fixed_asset_disposal,
)
from app.api.permissions import require_company_permission


router = APIRouter(
    prefix=(
        "/companies/{company_id}/fixed-assets/"
        "{fixed_asset_id}/disposals"
    ),
    tags=["fixed-asset-disposals"],
)


@router.get(
    "",
    response_model=list[FixedAssetDisposalRead],
)
async def get_fixed_asset_disposals(
    company_id: int,
    fixed_asset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    try:
        return await list_fixed_asset_disposals(
            db,
            company_id,
            fixed_asset_id,
        )
    except FixedAssetDisposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post(
    "",
    response_model=FixedAssetDisposalRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_fixed_asset_disposal(
    company_id: int,
    fixed_asset_id: int,
    payload: FixedAssetDisposalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission(
            "journal_entries.create"
        )
    ),
):
    try:
        row = await create_fixed_asset_disposal(
            db,
            company_id,
            fixed_asset_id,
            payload,
            current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except FixedAssetDisposalError as exc:
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
    response_model=FixedAssetDisposalRead,
)
async def post_fixed_asset_disposal_reversal(
    company_id: int,
    fixed_asset_id: int,
    operation_id: int,
    payload: FixedAssetDisposalReverse,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission(
            "journal_entries.create"
        )
    ),
):
    try:
        row = await reverse_fixed_asset_disposal(
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
    except FixedAssetDisposalError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise
