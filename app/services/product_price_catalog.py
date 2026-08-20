from datetime import date

from app.services.product_price_definition import (
    ProductPriceDefinition,
)


class ProductPriceCatalogError(Exception):
    """Base error for product price catalog operations."""


class ProductPriceNotFoundError(ProductPriceCatalogError):
    """Raised when a product price cannot be found."""


class DuplicateProductPriceError(ProductPriceCatalogError):
    """Raised when an exact effective-dated price is duplicated."""


class ProductPriceCatalog:
    def __init__(
        self,
        prices: tuple[ProductPriceDefinition, ...],
    ) -> None:
        self._by_key: dict[
            tuple[int, int, str, str, date],
            ProductPriceDefinition,
        ] = {}

        for price in prices:
            key = (
                price.company_id,
                price.product_id,
                price.price_type_code,
                price.uom_code,
                price.effective_from,
            )

            if key in self._by_key:
                raise DuplicateProductPriceError(
                    "Duplicate product price: "
                    f"company_id={price.company_id}, "
                    f"product_id={price.product_id}, "
                    f"price_type_code='{price.price_type_code}', "
                    f"uom_code='{price.uom_code}', "
                    f"effective_from={price.effective_from}"
                )

            self._by_key[key] = price

    def get_exact(
        self,
        company_id: int,
        product_id: int,
        price_type_code: str,
        uom_code: str,
        effective_from: date,
    ) -> ProductPriceDefinition:
        key = (
            company_id,
            product_id,
            price_type_code,
            uom_code,
            effective_from,
        )

        try:
            return self._by_key[key]
        except KeyError as exc:
            raise ProductPriceNotFoundError(
                "Exact product price not found: "
                f"company_id={company_id}, "
                f"product_id={product_id}, "
                f"price_type_code='{price_type_code}', "
                f"uom_code='{uom_code}', "
                f"effective_from={effective_from}"
            ) from exc

    def get_effective(
        self,
        company_id: int,
        product_id: int,
        price_type_code: str,
        uom_code: str,
        effective_date: date,
    ) -> ProductPriceDefinition:
        candidates = tuple(
            price
            for price in self._by_key.values()
            if (
                price.company_id == company_id
                and price.product_id == product_id
                and price.price_type_code == price_type_code
                and price.uom_code == uom_code
                and price.effective_from <= effective_date
            )
        )

        if not candidates:
            raise ProductPriceNotFoundError(
                "No effective product price found: "
                f"company_id={company_id}, "
                f"product_id={product_id}, "
                f"price_type_code='{price_type_code}', "
                f"uom_code='{uom_code}', "
                f"effective_date={effective_date}"
            )

        return max(
            candidates,
            key=lambda price: price.effective_from,
        )

    def get_effective_by_uom(
        self,
        company_id: int,
        product_id: int,
        price_type_code: str,
        effective_date: date,
    ) -> tuple[ProductPriceDefinition, ...]:
        """
        Return the latest effective price for each UOM
        for a product and price type.
        """

        candidates = tuple(
            price
            for price in self._by_key.values()
            if (
                price.company_id == company_id
                and price.product_id == product_id
                and price.price_type_code == price_type_code
                and price.effective_from <= effective_date
            )
        )

        latest_by_uom: dict[
            str,
            ProductPriceDefinition,
        ] = {}

        for price in candidates:
            current = latest_by_uom.get(
                price.uom_code
            )

            if (
                current is None
                or price.effective_from
                > current.effective_from
            ):
                latest_by_uom[
                    price.uom_code
                ] = price

        return tuple(
            latest_by_uom.values()
        )

    def all(
        self,
    ) -> tuple[ProductPriceDefinition, ...]:
        return tuple(self._by_key.values())