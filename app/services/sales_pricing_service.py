from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price_type import PriceType
from app.models.product import Product
from app.services.price_types import PriceKind
from app.services.trade_document_types import (
    TradeDirection,
)
from app.services.pricing_master_service import (
    PricingMasterError,
    PricingMasterPriceTypeNotFoundError,
    get_effective_product_price,
)


class SalesPricingError(Exception):
    pass


class SalesPricingConfigurationError(
    SalesPricingError
):
    pass


class SalesPricingNotFoundError(
    SalesPricingError
):
    pass


@dataclass(frozen=True)
class ResolvedSalesPrice:
    unit_price: Decimal
    price_type_code: str
    source_product_price_id: int
    price_uom_code: str
    price_effective_from: date


async def resolve_sales_master_price(
    db: AsyncSession,
    *,
    company_id: int,
    product_id: int,
    price_type_code: str,
    document_date: date,
    document_currency_code: str,
    document_direction: TradeDirection,
) -> ResolvedSalesPrice:
    if document_direction != TradeDirection.SALE:
        raise SalesPricingConfigurationError(
            "Master sales pricing is available "
            "only for sale documents"
        )

    product = (
        await db.execute(
            select(Product).where(
                Product.id == product_id,
                Product.company_id == company_id,
            )
        )
    ).scalar_one_or_none()

    if product is None:
        raise SalesPricingConfigurationError(
            "Product not found for company"
        )

    if product.base_uom_code is None:
        raise SalesPricingConfigurationError(
            "Product base UOM is required "
            "for master pricing"
        )

    normalized_code = (
        price_type_code.strip().upper()
    )

    price_type = (
        await db.execute(
            select(PriceType).where(
                PriceType.company_id == company_id,
                PriceType.code == normalized_code,
            )
        )
    ).scalar_one_or_none()

    if price_type is None:
        raise SalesPricingNotFoundError(
            "Sales price type not found"
        )

    if price_type.kind != PriceKind.SALES.value:
        raise SalesPricingConfigurationError(
            "Price type must be a sales price type"
        )

    if (
        price_type.currency_code
        != document_currency_code.strip().upper()
    ):
        raise SalesPricingConfigurationError(
            "Price type currency must match "
            "trade document currency"
        )

    try:
        product_price = (
            await get_effective_product_price(
                db,
                company_id=company_id,
                product_id=product_id,
                price_type_code=normalized_code,
                uom_code=product.base_uom_code,
                effective_date=document_date,
            )
        )
    except PricingMasterPriceTypeNotFoundError as exc:
        raise SalesPricingNotFoundError(
            "No effective sales product price found"
        ) from exc
    except PricingMasterError as exc:
        raise SalesPricingConfigurationError(
            str(exc)
        ) from exc

    if (
        product_price.uom_code
        != product.base_uom_code
    ):
        raise SalesPricingConfigurationError(
            "Resolved price UOM does not match "
            "product base UOM"
        )

    return ResolvedSalesPrice(
        unit_price=product_price.amount,
        price_type_code=normalized_code,
        source_product_price_id=product_price.id,
        price_uom_code=product_price.uom_code,
        price_effective_from=(
            product_price.effective_from
        ),
    )
