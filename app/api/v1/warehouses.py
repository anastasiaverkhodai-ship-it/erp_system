from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.company import Company
from app.models.warehouse import Warehouse
from app.schemas.warehouse import (
    WarehouseCreate,
    WarehouseResponse,
    WarehouseUpdate,
)


router = APIRouter(
    prefix="/companies/{company_id}/warehouses",
    tags=["Warehouses"],
)


# ---------------------------------------------------------
# GET WAREHOUSE LIST
# ---------------------------------------------------------


@router.get(
    "",
    response_model=list[WarehouseResponse],
)
async def get_warehouses(
    company_id: int,
    _=Depends(
        require_company_permission(
            "warehouse.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Warehouse)
        .where(
            Warehouse.company_id == company_id,
        )
        .order_by(
            Warehouse.name.asc(),
            Warehouse.id.asc(),
        )
    )

    return result.scalars().all()


# ---------------------------------------------------------
# GET ONE WAREHOUSE
# ---------------------------------------------------------


@router.get(
    "/{warehouse_id}",
    response_model=WarehouseResponse,
)
async def get_warehouse(
    company_id: int,
    warehouse_id: int,
    _=Depends(
        require_company_permission(
            "warehouse.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Warehouse).where(
            Warehouse.id == warehouse_id,
            Warehouse.company_id == company_id,
        )
    )

    warehouse = result.scalar_one_or_none()

    if warehouse is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Warehouse not found",
        )

    return warehouse


# ---------------------------------------------------------
# CREATE WAREHOUSE
# ---------------------------------------------------------


@router.post(
    "",
    response_model=WarehouseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_warehouse(
    company_id: int,
    data: WarehouseCreate,
    _=Depends(
        require_company_permission(
            "warehouse.create"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        company_result = await db.execute(
            select(Company).where(
                Company.id == company_id,
                Company.is_active.is_(True),
            )
        )

        company = company_result.scalar_one_or_none()

        if company is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found",
            )

        existing_result = await db.execute(
            select(Warehouse.id).where(
                Warehouse.company_id == company_id,
                Warehouse.name == data.name,
            )
        )

        if (
            existing_result.scalar_one_or_none()
            is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Warehouse name already exists "
                    "in this company"
                ),
            )

        warehouse = Warehouse(
            company_id=company_id,
            name=data.name,
            is_active=True,
        )

        db.add(warehouse)

        await db.commit()
        await db.refresh(warehouse)

        return warehouse

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Warehouse could not be created "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------
# UPDATE WAREHOUSE
# ---------------------------------------------------------


@router.patch(
    "/{warehouse_id}",
    response_model=WarehouseResponse,
)
async def update_warehouse(
    company_id: int,
    warehouse_id: int,
    data: WarehouseUpdate,
    _=Depends(
        require_company_permission(
            "warehouse.update"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(Warehouse)
            .where(
                Warehouse.id == warehouse_id,
                Warehouse.company_id == company_id,
            )
            .with_for_update()
        )

        warehouse = result.scalar_one_or_none()

        if warehouse is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Warehouse not found",
            )

        update_data = data.model_dump(
            exclude_unset=True
        )

        if (
            "name" in update_data
            and update_data["name"] != warehouse.name
        ):
            existing_result = await db.execute(
                select(Warehouse.id).where(
                    Warehouse.company_id == company_id,
                    Warehouse.name == update_data["name"],
                    Warehouse.id != warehouse.id,
                )
            )

            if (
                existing_result.scalar_one_or_none()
                is not None
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Warehouse name already exists "
                        "in this company"
                    ),
                )

        for field, value in update_data.items():
            setattr(
                warehouse,
                field,
                value,
            )

        await db.commit()
        await db.refresh(warehouse)

        return warehouse

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Warehouse could not be updated "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise