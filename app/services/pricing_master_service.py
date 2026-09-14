from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.price_type import PriceType
from app.models.product import Product
from app.models.product_price import ProductPrice
from app.services.price_types import PriceKind


class PricingMasterError(Exception):
    pass


class PricingMasterCompanyNotFoundError(PricingMasterError):
    pass


class PricingMasterProductNotFoundError(PricingMasterError):
    pass


class PricingMasterPriceTypeNotFoundError(PricingMasterError):
    pass


class PricingMasterDuplicateError(PricingMasterError):
    pass


def _normalize_code(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise PricingMasterError("Price type code cannot be blank")
    return normalized


def _normalize_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise PricingMasterError("Price type name cannot be blank")
    return normalized


def _normalize_currency(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise PricingMasterError(
            "Currency code must contain exactly 3 letters"
        )
    return normalized


def _normalize_uom(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise PricingMasterError("UOM code cannot be blank")
    return normalized


async def create_price_type(
    db: AsyncSession,
    *,
    company_id: int,
    code: str,
    name: str,
    kind: PriceKind,
    currency_code: str,
) -> PriceType:
    company = (
        await db.execute(
            select(Company).where(
                Company.id == company_id,
                Company.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if company is None:
        raise PricingMasterCompanyNotFoundError(
            "Active company not found"
        )

    normalized_code = _normalize_code(code)

    existing = (
        await db.execute(
            select(PriceType.id).where(
                PriceType.company_id == company_id,
                PriceType.code == normalized_code,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise PricingMasterDuplicateError(
            "Price type code already exists in this company"
        )

    price_type = PriceType(
        company_id=company_id,
        code=normalized_code,
        name=_normalize_name(name),
        kind=kind.value,
        currency_code=_normalize_currency(currency_code),
    )

    db.add(price_type)
    await db.flush()
    return price_type


async def create_product_price(
    db: AsyncSession,
    *,
    company_id: int,
    product_id: int,
    price_type_code: str,
    amount: Decimal,
    uom_code: str,
    effective_from: date,
) -> ProductPrice:
    if amount < 0:
        raise PricingMasterError(
            "Price amount cannot be negative"
        )

    normalized_price_type = _normalize_code(
        price_type_code
    )
    normalized_uom = _normalize_uom(uom_code)

    product = (
        await db.execute(
            select(Product.id).where(
                Product.company_id == company_id,
                Product.id == product_id,
                Product.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if product is None:
        raise PricingMasterProductNotFoundError(
            "Active product not found"
        )

    price_type = (
        await db.execute(
            select(PriceType.id).where(
                PriceType.company_id == company_id,
                PriceType.code == normalized_price_type,
            )
        )
    ).scalar_one_or_none()

    if price_type is None:
        raise PricingMasterPriceTypeNotFoundError(
            "Price type not found"
        )

    existing = (
        await db.execute(
            select(ProductPrice.id).where(
                ProductPrice.company_id == company_id,
                ProductPrice.product_id == product_id,
                ProductPrice.price_type_code
                == normalized_price_type,
                ProductPrice.uom_code == normalized_uom,
                ProductPrice.effective_from
                == effective_from,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise PricingMasterDuplicateError(
            "Product price already exists for this effective date"
        )

    price = ProductPrice(
        company_id=company_id,
        product_id=product_id,
        price_type_code=normalized_price_type,
        amount=amount,
        uom_code=normalized_uom,
        effective_from=effective_from,
    )

    db.add(price)
    await db.flush()
    return price


async def get_effective_product_price(
    db: AsyncSession,
    *,
    company_id: int,
    product_id: int,
    price_type_code: str,
    uom_code: str,
    effective_date: date,
) -> ProductPrice:
    price = (
        await db.execute(
            select(ProductPrice)
            .where(
                ProductPrice.company_id == company_id,
                ProductPrice.product_id == product_id,
                ProductPrice.price_type_code
                == _normalize_code(price_type_code),
                ProductPrice.uom_code
                == _normalize_uom(uom_code),
                ProductPrice.effective_from
                <= effective_date,
            )
            .order_by(
                ProductPrice.effective_from.desc(),
                ProductPrice.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if price is None:
        raise PricingMasterPriceTypeNotFoundError(
            "No effective product price found"
        )

    return price
