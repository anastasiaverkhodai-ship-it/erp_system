from app.services.price_type_definition import (
    PriceTypeDefinition,
)


class PriceTypeCatalogError(Exception):
    """Base error for price type catalog operations."""


class PriceTypeNotFoundError(PriceTypeCatalogError):
    """Raised when a price type cannot be found."""


class DuplicatePriceTypeError(PriceTypeCatalogError):
    """Raised when a company has duplicate price type codes."""


class PriceTypeCatalog:
    def __init__(
        self,
        price_types: tuple[PriceTypeDefinition, ...],
    ) -> None:
        self._by_key: dict[
            tuple[int, str],
            PriceTypeDefinition,
        ] = {}

        for price_type in price_types:
            key = (
                price_type.company_id,
                price_type.code,
            )

            if key in self._by_key:
                raise DuplicatePriceTypeError(
                    "Duplicate price type: "
                    f"company_id={price_type.company_id}, "
                    f"code='{price_type.code}'"
                )

            self._by_key[key] = price_type

    def get(
        self,
        company_id: int,
        code: str,
    ) -> PriceTypeDefinition:
        key = (company_id, code)

        try:
            return self._by_key[key]
        except KeyError as exc:
            raise PriceTypeNotFoundError(
                "Price type not found: "
                f"company_id={company_id}, "
                f"code='{code}'"
            ) from exc

    def for_company(
        self,
        company_id: int,
    ) -> tuple[PriceTypeDefinition, ...]:
        return tuple(
            price_type
            for price_type in self._by_key.values()
            if price_type.company_id == company_id
        )

    def all(
        self,
    ) -> tuple[PriceTypeDefinition, ...]:
        return tuple(self._by_key.values())