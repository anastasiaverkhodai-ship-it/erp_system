from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.models.fixed_asset import FixedAsset
from app.models.fixed_asset_commissioning import FixedAssetCommissioning
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.services.fixed_asset_service import FixedAssetValidationError
from app.services.fixed_asset_acquisition_service import FixedAssetAcquisitionError
from app.services.accounting_posting import AccountingPostingError
from app.services.accounting_reversal import AccountingReversalError
from app.schemas.fixed_asset_commissioning import (
    FixedAssetCommissioningCreate,
    FixedAssetCommissioningRead,
    FixedAssetCommissioningReverse,
)
from app.services.fixed_asset_commissioning_service import (
    FixedAssetCommissioningError,
    FixedAssetCommissioningNotFoundError,
    create_and_post_commissioning,
    reverse_commissioning,
)


router = APIRouter(
    prefix=(
        "/companies/{company_id}/fixed-assets/"
        "{fixed_asset_id}/commissionings"
    ),
    tags=["fixed-asset-commissionings"],
)


def _raise_service_error(
    exc: FixedAssetCommissioningError,
) -> None:
    if isinstance(
        exc,
        FixedAssetCommissioningNotFoundError,
    ):
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )
    raise HTTPException(
        status_code=400,
        detail=str(exc),
    )


@router.post(
    "",
    response_model=FixedAssetCommissioningRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_fixed_asset_commissioning(
    company_id: int,
    fixed_asset_id: int,
    payload: FixedAssetCommissioningCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_company_permission("journal_entries.post")),
):
    try:
        row = await create_and_post_commissioning(
            db,
            company_id=company_id,
            fixed_asset_id=fixed_asset_id,
            request_key=payload.request_key,
            commissioning_date=payload.commissioning_date,
            created_by=current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (FixedAssetCommissioningError, FixedAssetValidationError,
            FixedAssetAcquisitionError, AccountingPostingError, AccountingReversalError) as exc:
        await db.rollback()
        _raise_service_error(exc)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="fixed asset commissioning conflict",
        )
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post(
    "/{commissioning_id}/reverse",
    response_model=FixedAssetCommissioningRead,
)
async def post_fixed_asset_commissioning_reversal(
    company_id: int,
    fixed_asset_id: int,
    commissioning_id: int,
    payload: FixedAssetCommissioningReverse,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_company_permission("journal_entries.reverse")),
):
    try:
        row = await reverse_commissioning(
            db,
            company_id=company_id,
            fixed_asset_id=fixed_asset_id,
            commissioning_id=commissioning_id,
            reversal_date=payload.reversal_date,
            reversed_by=current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (FixedAssetCommissioningError, FixedAssetValidationError,
            FixedAssetAcquisitionError, AccountingPostingError, AccountingReversalError) as exc:
        await db.rollback()
        _raise_service_error(exc)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="fixed asset commissioning reversal conflict",
        )
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.get("", response_model=list[FixedAssetCommissioningRead])
async def get_fixed_asset_commissionings(
    company_id: int, fixed_asset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_company_permission("journal_entries.read")),
):
    asset = await db.scalar(select(FixedAsset.id).where(
        FixedAsset.company_id == company_id, FixedAsset.id == fixed_asset_id))
    if asset is None:
        raise HTTPException(404, "fixed asset not found")
    return list((await db.scalars(select(FixedAssetCommissioning).where(
        FixedAssetCommissioning.company_id == company_id,
        FixedAssetCommissioning.fixed_asset_id == fixed_asset_id,
    ).order_by(FixedAssetCommissioning.commissioning_date, FixedAssetCommissioning.id))).all())
