from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.price_type_definition import (
    PriceTypeDefinition,
)
from app.services.product_price_definition import (
    ProductPriceDefinition,
)


@dataclass(frozen=True, slots=True)
class PriceLookupResult:
    """
    Resolved effective product price.

    The stored product price may use a different UOM
    from the UOM requested by the caller.
    """

    price_type: PriceTypeDefinition
    product_price: ProductPriceDefinition
    resolved_amount: Decimal
    resolved_uom_code: str

    def __post_init__(self) -> None:
        if (
            self.price_type.company_id
            != self.product_price.company_id
        ):
            raise ValueError(
                "Price type company does not match "
                "product price company"
            )

        if (
            self.price_type.code
            != self.product_price.price_type_code
        ):
            raise ValueError(
                "Price type code does not match "
                "product price type code"
            )

        if self.resolved_amount < 0:
            raise ValueError(
                "Resolved price amount cannot be negative"
            )

        if not self.resolved_uom_code.strip():
            raise ValueError(
                "Resolved UOM code cannot be empty"
            )

    @property
    def company_id(self) -> int:
        return self.product_price.company_id

    @property
    def product_id(self) -> int:
        return self.product_price.product_id

    @property
    def price_type_code(self) -> str:
        return self.price_type.code

    @property
    def amount(self) -> Decimal:
        return self.resolved_amount

    @property
    def currency_code(self) -> str:
        return self.price_type.currency_code

    @property
    def uom_code(self) -> str:
        return self.resolved_uom_code

    @property
    def effective_from(self) -> date:
        return self.product_price.effective_from

    @property
    def source_amount(self) -> Decimal:
        return self.product_price.amount

    @property
    def source_uom_code(self) -> str:
        return self.product_price.uom_code

    @property
    def was_converted(self) -> bool:
        return (
            self.product_price.uom_code
            != self.resolved_uom_code
        )