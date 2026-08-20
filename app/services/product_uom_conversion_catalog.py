from decimal import Decimal

from app.services.product_uom_conversion_definition import (
    ProductUnitConversionDefinition,
)


class ProductUnitConversionCatalogError(Exception):
    """Base error for product unit conversion catalog operations."""


class ProductUnitConversionNotFoundError(
    ProductUnitConversionCatalogError
):
    """Raised when no product-specific conversion exists."""


class DuplicateProductUnitConversionError(
    ProductUnitConversionCatalogError
):
    """Raised when the same product unit pair is registered twice."""


class ProductUnitConversionCatalog:
    def __init__(
        self,
        conversions: tuple[
            ProductUnitConversionDefinition,
            ...,
        ],
    ) -> None:
        self._conversions: dict[
            tuple[int, str, str],
            ProductUnitConversionDefinition,
        ] = {}

        for conversion in conversions:
            direct_key = (
                conversion.product_id,
                conversion.source_code,
                conversion.target_code,
            )

            reverse_key = (
                conversion.product_id,
                conversion.target_code,
                conversion.source_code,
            )

            if (
                direct_key in self._conversions
                or reverse_key in self._conversions
            ):
                raise DuplicateProductUnitConversionError(
                    "Duplicate product unit conversion pair: "
                    f"product_id={conversion.product_id}, "
                    f"'{conversion.source_code}' "
                    f"<-> '{conversion.target_code}'"
                )

            self._conversions[direct_key] = conversion

    def get_factor(
        self,
        product_id: int,
        source_code: str,
        target_code: str,
    ) -> Decimal:
        direct = self._conversions.get(
            (
                product_id,
                source_code,
                target_code,
            )
        )

        if direct is not None:
            return direct.factor

        reverse = self._conversions.get(
            (
                product_id,
                target_code,
                source_code,
            )
        )

        if reverse is not None:
            return Decimal("1") / reverse.factor

        raise ProductUnitConversionNotFoundError(
            "Product unit conversion is not registered: "
            f"product_id={product_id}, "
            f"'{source_code}' -> '{target_code}'"
        )

    def all(
        self,
    ) -> tuple[ProductUnitConversionDefinition, ...]:
        return tuple(self._conversions.values())