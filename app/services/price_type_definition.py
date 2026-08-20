from dataclasses import dataclass

from app.services.price_types import (
    PriceKind,
)


@dataclass(frozen=True, slots=True)
class PriceTypeDefinition:
    """
    Company-specific price type.

    Examples:
        Retail
        Wholesale
        Supplier Purchase
        Internal Transfer
    """

    company_id: int
    code: str
    name: str
    kind: PriceKind
    currency_code: str

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "Company ID must be greater than zero"
            )

        if not self.code.strip():
            raise ValueError(
                "Price type code cannot be empty"
            )

        if not self.name.strip():
            raise ValueError(
                "Price type name cannot be empty"
            )

        if (
            len(self.currency_code) != 3
            or not self.currency_code.isalpha()
            or self.currency_code
            != self.currency_code.upper()
        ):
            raise ValueError(
                "Currency code must contain exactly "
                "3 uppercase letters"
            )