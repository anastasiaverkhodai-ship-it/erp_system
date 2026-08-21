from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class BatchDefinition:
    """
    Product batch / lot definition.

    A batch belongs to one company and one product.

    production_date
        Optional manufacturing / production date.

    expiry_date
        Optional expiration date.
    """

    company_id: int
    product_id: int
    batch_number: str
    production_date: date | None = None
    expiry_date: date | None = None

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "Company ID must be greater than zero"
            )

        if self.product_id <= 0:
            raise ValueError(
                "Product ID must be greater than zero"
            )

        if not self.batch_number.strip():
            raise ValueError(
                "Batch number cannot be empty"
            )

        if (
            self.production_date is not None
            and self.expiry_date is not None
            and self.expiry_date < self.production_date
        ):
            raise ValueError(
                "Expiry date cannot be earlier "
                "than production date"
            )

    @property
    def has_expiry_date(self) -> bool:
        return self.expiry_date is not None