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
from app.models.product import Product
from app.schemas.product import (
    ProductCreate,
    ProductResponse,
    ProductUpdate,
)
from app.services.uom_catalog_service import (
    SYSTEM_UOM_CATALOG,
    UnitOfMeasureNotFoundError,
)


router = APIRouter(
    prefix="/companies/{company_id}/products",
    tags=["Products"],
)


def _normalize_base_uom_code(
    value: str,
) -> str:
    code = value.strip().lower()

    try:
        SYSTEM_UOM_CATALOG.get(code)
    except UnitOfMeasureNotFoundError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=(
                "Product base UOM is not registered"
            ),
        ) from exc

    return code


# ---------------------------------------------------------
# GET PRODUCT LIST
# ---------------------------------------------------------


@router.get(
    "",
    response_model=list[ProductResponse],
)
async def get_products(
    company_id: int,
    _=Depends(
        require_company_permission(
            "products.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Product)
        .where(
            Product.company_id == company_id,
        )
        .order_by(
            Product.name.asc(),
            Product.id.asc(),
        )
    )

    return result.scalars().all()


# ---------------------------------------------------------
# GET ONE PRODUCT
# ---------------------------------------------------------


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
)
async def get_product(
    company_id: int,
    product_id: int,
    _=Depends(
        require_company_permission(
            "products.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Product).where(
            Product.id == product_id,
            Product.company_id == company_id,
        )
    )

    product = result.scalar_one_or_none()

    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    return product


# ---------------------------------------------------------
# CREATE PRODUCT
# ---------------------------------------------------------


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_product(
    company_id: int,
    data: ProductCreate,
    _=Depends(
        require_company_permission(
            "products.create"
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
            select(Product.id).where(
                Product.company_id == company_id,
                Product.sku == data.sku,
            )
        )

        if (
            existing_result.scalar_one_or_none()
            is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Product SKU already exists "
                    "in this company"
                ),
            )

        base_uom_code = _normalize_base_uom_code(
            data.base_uom_code
        )

        product = Product(
            company_id=company_id,
            name=data.name,
            sku=data.sku,
            base_uom_code=base_uom_code,
            is_active=True,
        )

        db.add(product)

        await db.commit()
        await db.refresh(product)

        return product

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Product could not be created "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------
# UPDATE PRODUCT
# ---------------------------------------------------------


@router.patch(
    "/{product_id}",
    response_model=ProductResponse,
)
async def update_product(
    company_id: int,
    product_id: int,
    data: ProductUpdate,
    _=Depends(
        require_company_permission(
            "products.update"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(Product)
            .where(
                Product.id == product_id,
                Product.company_id == company_id,
            )
            .with_for_update()
        )

        product = result.scalar_one_or_none()

        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found",
            )

        update_data = data.model_dump(
            exclude_unset=True
        )

        if (
            "sku" in update_data
            and update_data["sku"] != product.sku
        ):
            existing_result = await db.execute(
                select(Product.id).where(
                    Product.company_id == company_id,
                    Product.sku == update_data["sku"],
                    Product.id != product.id,
                )
            )

            if (
                existing_result.scalar_one_or_none()
                is not None
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Product SKU already exists "
                        "in this company"
                    ),
                )

        if "base_uom_code" in update_data:
            requested_base_uom = (
                _normalize_base_uom_code(
                    update_data["base_uom_code"]
                )
            )

            if (
                product.base_uom_code is not None
                and requested_base_uom
                != product.base_uom_code
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Product base UOM cannot be "
                        "changed after it has been "
                        "assigned"
                    ),
                )

            update_data["base_uom_code"] = (
                requested_base_uom
            )

        for field, value in update_data.items():
            setattr(
                product,
                field,
                value,
            )

        await db.commit()
        await db.refresh(product)

        return product

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Product could not be updated "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise