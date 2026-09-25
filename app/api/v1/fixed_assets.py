from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.fixed_asset import (
    FixedAssetCardHistoryResponse,
    FixedAssetCreate,
    FixedAssetGroupCreate,
    FixedAssetGroupResponse,
    FixedAssetGroupUpdate,
    FixedAssetLocationCreate,
    FixedAssetLocationResponse,
    FixedAssetLocationUpdate,
    FixedAssetResponsiblePersonCreate,
    FixedAssetResponsiblePersonResponse,
    FixedAssetResponsiblePersonUpdate,
    FixedAssetResponse,
    FixedAssetUpdate,
)
from app.services.fixed_asset_service import (
    FixedAssetNotFoundError,
    FixedAssetValidationError,
    create_fixed_asset,
    create_fixed_asset_group,
    create_fixed_asset_location,
    create_fixed_asset_responsible_person,
    get_fixed_asset,
    list_fixed_asset_card_history,
    list_fixed_asset_groups,
    list_fixed_asset_locations,
    list_fixed_asset_responsible_persons,
    list_fixed_assets,
    update_fixed_asset,
    update_fixed_asset_group,
    update_fixed_asset_location,
    update_fixed_asset_responsible_person,
)


router = APIRouter(
    prefix="/companies/{company_id}/fixed-assets",
    tags=["Fixed Assets"],
)


def _raise_service_error(exc: Exception):
    if isinstance(exc, FixedAssetNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FixedAssetValidationError):
        raise HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.get("/groups", response_model=list[FixedAssetGroupResponse])
async def get_groups(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await list_fixed_asset_groups(db, company_id)


@router.post(
    "/groups",
    response_model=FixedAssetGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_group(
    company_id: int,
    payload: FixedAssetGroupCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = await create_fixed_asset_group(db, company_id, payload)
        await db.commit()
        await db.refresh(row)
        return row
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="fixed asset group conflicts with existing data",
        )
    except Exception:
        await db.rollback()
        raise


@router.patch("/groups/{group_id}", response_model=FixedAssetGroupResponse)
async def patch_group(
    company_id: int,
    group_id: int,
    payload: FixedAssetGroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = await update_fixed_asset_group(
            db,
            company_id,
            group_id,
            payload,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (FixedAssetNotFoundError, FixedAssetValidationError) as exc:
        await db.rollback()
        _raise_service_error(exc)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="fixed asset group conflict")
    except Exception:
        await db.rollback()
        raise


@router.get("/locations", response_model=list[FixedAssetLocationResponse])
async def get_locations(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await list_fixed_asset_locations(db, company_id)


@router.post(
    "/locations",
    response_model=FixedAssetLocationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_location(
    company_id: int,
    payload: FixedAssetLocationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = await create_fixed_asset_location(db, company_id, payload)
        await db.commit()
        await db.refresh(row)
        return row
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="fixed asset location conflicts with existing data",
        )
    except Exception:
        await db.rollback()
        raise


@router.patch(
    "/locations/{location_id}",
    response_model=FixedAssetLocationResponse,
)
async def patch_location(
    company_id: int,
    location_id: int,
    payload: FixedAssetLocationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = await update_fixed_asset_location(
            db,
            company_id,
            location_id,
            payload,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (FixedAssetNotFoundError, FixedAssetValidationError) as exc:
        await db.rollback()
        _raise_service_error(exc)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="fixed asset location conflict")
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/responsible-persons",
    response_model=list[FixedAssetResponsiblePersonResponse],
)
async def get_responsible_persons(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await list_fixed_asset_responsible_persons(db, company_id)


@router.post(
    "/responsible-persons",
    response_model=FixedAssetResponsiblePersonResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_responsible_person(
    company_id: int,
    payload: FixedAssetResponsiblePersonCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = await create_fixed_asset_responsible_person(
            db,
            company_id,
            payload,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="fixed asset responsible person conflicts with existing data",
        )
    except Exception:
        await db.rollback()
        raise


@router.patch(
    "/responsible-persons/{responsible_person_id}",
    response_model=FixedAssetResponsiblePersonResponse,
)
async def patch_responsible_person(
    company_id: int,
    responsible_person_id: int,
    payload: FixedAssetResponsiblePersonUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = await update_fixed_asset_responsible_person(
            db,
            company_id,
            responsible_person_id,
            payload,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (FixedAssetNotFoundError, FixedAssetValidationError) as exc:
        await db.rollback()
        _raise_service_error(exc)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="fixed asset responsible person conflict",
        )
    except Exception:
        await db.rollback()
        raise


@router.get("", response_model=list[FixedAssetResponse])
async def get_assets(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await list_fixed_assets(db, company_id)


@router.get("/{fixed_asset_id}", response_model=FixedAssetResponse)
async def get_asset(
    company_id: int,
    fixed_asset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await get_fixed_asset(db, company_id, fixed_asset_id)
    except FixedAssetNotFoundError as exc:
        _raise_service_error(exc)


@router.post(
    "",
    response_model=FixedAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_asset(
    company_id: int,
    payload: FixedAssetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = await create_fixed_asset(
            db,
            company_id,
            payload,
            current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (FixedAssetNotFoundError, FixedAssetValidationError) as exc:
        await db.rollback()
        _raise_service_error(exc)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="fixed asset conflict")
    except Exception:
        await db.rollback()
        raise


@router.patch("/{fixed_asset_id}", response_model=FixedAssetResponse)
async def patch_asset(
    company_id: int,
    fixed_asset_id: int,
    payload: FixedAssetUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = await update_fixed_asset(
            db,
            company_id,
            fixed_asset_id,
            payload,
            current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (FixedAssetNotFoundError, FixedAssetValidationError) as exc:
        await db.rollback()
        _raise_service_error(exc)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="fixed asset conflict")
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/{fixed_asset_id}/history",
    response_model=list[FixedAssetCardHistoryResponse],
)
async def get_asset_history(
    company_id: int,
    fixed_asset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await list_fixed_asset_card_history(
            db,
            company_id,
            fixed_asset_id,
        )
    except FixedAssetNotFoundError as exc:
        _raise_service_error(exc)
