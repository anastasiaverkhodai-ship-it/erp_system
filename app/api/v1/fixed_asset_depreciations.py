from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.permissions import require_company_permission
from app.models.user import User

from app.models.fixed_asset import FixedAsset
from app.models.fixed_asset_depreciation import FixedAssetDepreciation
from app.schemas.fixed_asset_depreciation import (
    FixedAssetDepreciationCreate,
    FixedAssetDepreciationRead,
    FixedAssetDepreciationReverse,
)
from app.services.accounting_posting import AccountingPostingError
from app.services.accounting_reversal import AccountingReversalError
from app.services.fixed_asset_depreciation_service import (
    FixedAssetDepreciationError,
    create_and_post_depreciation,
    reverse_depreciation,
)


router = APIRouter(
    prefix=(
        "/companies/{company_id}/fixed-assets/"
        "{fixed_asset_id}/depreciations"
    ),
    tags=["fixed-asset-depreciations"],
)


def _raise_service_error(exc: Exception) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(exc),
    ) from exc


@router.post(
    "",
    response_model=FixedAssetDepreciationRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_fixed_asset_depreciation(
    company_id: int,
    fixed_asset_id: int,
    payload: FixedAssetDepreciationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission(
            "journal_entries.post"
        )
    ),
):
    try:
        row = await create_and_post_depreciation(
            db,
            company_id=company_id,
            fixed_asset_id=fixed_asset_id,
            request_key=payload.request_key,
            period_start=payload.period_start,
            period_end=payload.period_end,
            posting_date=payload.posting_date,
            created_by=current_user.id,
        )

        await db.commit()
        await db.refresh(row)

        return row

    except (
        FixedAssetDepreciationError,
        AccountingPostingError,
        AccountingReversalError,
    ) as exc:
        await db.rollback()
        _raise_service_error(exc)

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="fixed asset depreciation conflict",
        ) from exc

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise


@router.post(
    "/{depreciation_id}/reverse",
    response_model=FixedAssetDepreciationRead,
)
async def post_fixed_asset_depreciation_reversal(
    company_id: int,
    fixed_asset_id: int,
    depreciation_id: int,
    payload: FixedAssetDepreciationReverse,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission(
            "journal_entries.reverse"
        )
    ),
):
    try:
        row = await reverse_depreciation(
            db,
            company_id=company_id,
            fixed_asset_id=fixed_asset_id,
            depreciation_id=depreciation_id,
            reversal_date=payload.reversal_date,
            reversed_by=current_user.id,
        )

        await db.commit()
        await db.refresh(row)

        return row

    except (
        FixedAssetDepreciationError,
        AccountingPostingError,
        AccountingReversalError,
    ) as exc:
        await db.rollback()
        _raise_service_error(exc)

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "fixed asset depreciation "
                "reversal conflict"
            ),
        ) from exc

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise


@router.get(
    "",
    response_model=list[FixedAssetDepreciationRead],
)
async def get_fixed_asset_depreciations(
    company_id: int,
    fixed_asset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    asset = await db.scalar(
        select(FixedAsset.id).where(
            FixedAsset.company_id == company_id,
            FixedAsset.id == fixed_asset_id,
        )
    )

    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="fixed asset not found",
        )

    rows = await db.scalars(
        select(FixedAssetDepreciation)
        .where(
            FixedAssetDepreciation.company_id
            == company_id,
            FixedAssetDepreciation.fixed_asset_id
            == fixed_asset_id,
        )
        .order_by(
            FixedAssetDepreciation.period_start,
            FixedAssetDepreciation.id,
        )
    )

    return list(rows.all())
