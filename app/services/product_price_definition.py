from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ProductPriceDefinition:
    """
    Effective-dated product price.

    company_id
        Company that owns the price.

    product_id
        Product whose price is defined.

    price_type_code
        Company-specific price type code,
        for example retail or wholesale.

    amount
        Price amount for one specified UOM.

    uom_code
        Unit of measure to which the price applies.

    effective_from
        Date from which the price becomes effective.
    """

    company_id: int
    product_id: int
    price_type_code: str
    amount: Decimal
    uom_code: str
    effective_from: date

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "Company ID must be greater than zero"
            )

        if self.product_id <= 0:
            raise ValueError(
                "Product ID must be greater than zero"
            )

        if not self.price_type_code.strip():
            raise ValueError(
                "Price type code cannot be empty"
            )

        if self.amount < 0:
            raise ValueError(
                "Price amount cannot be negative"
            )

        if not self.uom_code.strip():
            raise ValueError(
                "UOM code cannot be empty"
            )