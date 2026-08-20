from datetime import date

from app.services.price_lookup_result import (
    PriceLookupResult,
)
from app.services.price_type_catalog import (
    PriceTypeCatalog,
)
from app.services.price_uom_conversion_service import (
    convert_product_price_uom,
)
from app.services.product_price_catalog import (
    ProductPriceCatalog,
    ProductPriceNotFoundError,
)
from app.services.product_uom_conversion_catalog import (
    ProductUnitConversionCatalog,
    ProductUnitConversionNotFoundError,
)


class AmbiguousPriceConversionError(Exception):
    """
    Raised when more than one stored UOM price
    can be converted to the requested UOM.
    """


def lookup_product_price(
    company_id: int,
    product_id: int,
    price_type_code: str,
    uom_code: str,
    effective_date: date,
    price_type_catalog: PriceTypeCatalog,
    product_price_catalog: ProductPriceCatalog,
    product_uom_conversion_catalog: (
        ProductUnitConversionCatalog | None
    ) = None,
) -> PriceLookupResult:
    """
    Resolve an effective product price.

    Exact UOM prices always have priority.

    If no exact price exists and a product UOM
    conversion catalog is supplied, compatible
    prices in other UOMs may be converted.
    """

    price_type = price_type_catalog.get(
        company_id=company_id,
        code=price_type_code,
    )

    try:
        product_price = product_price_catalog.get_effective(
            company_id=company_id,
            product_id=product_id,
            price_type_code=price_type_code,
            uom_code=uom_code,
            effective_date=effective_date,
        )

        return PriceLookupResult(
            price_type=price_type,
            product_price=product_price,
            resolved_amount=product_price.amount,
            resolved_uom_code=product_price.uom_code,
        )

    except ProductPriceNotFoundError as exact_error:
        if product_uom_conversion_catalog is None:
            raise

        candidates = (
            product_price_catalog.get_effective_by_uom(
                company_id=company_id,
                product_id=product_id,
                price_type_code=price_type_code,
                effective_date=effective_date,
            )
        )

        converted_candidates = []

        for candidate in candidates:
            try:
                converted_amount = (
                    convert_product_price_uom(
                        amount=candidate.amount,
                        product_id=product_id,
                        source_uom_code=candidate.uom_code,
                        target_uom_code=uom_code,
                        catalog=(
                            product_uom_conversion_catalog
                        ),
                    )
                )
            except ProductUnitConversionNotFoundError:
                continue

            converted_candidates.append(
                (
                    candidate,
                    converted_amount,
                )
            )

        if not converted_candidates:
            raise exact_error

        if len(converted_candidates) > 1:
            source_uoms = ", ".join(
                candidate.uom_code
                for candidate, _ in converted_candidates
            )

            raise AmbiguousPriceConversionError(
                "Multiple product prices can be converted "
                "to the requested UOM: "
                f"product_id={product_id}, "
                f"requested_uom='{uom_code}', "
                f"source_uoms=[{source_uoms}]"
            )

        product_price, converted_amount = (
            converted_candidates[0]
        )

        return PriceLookupResult(
            price_type=price_type,
            product_price=product_price,
            resolved_amount=converted_amount,
            resolved_uom_code=uom_code,
        )