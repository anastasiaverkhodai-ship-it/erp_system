from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class InventoryTrackingBalance:
    """
    Inventory balance for a tracked stock identity.

    The balance may represent:

    - total product stock in a warehouse;
    - one batch in a warehouse;
    - one serial number in a warehouse;
    - one serial number belonging to a batch.
    """

    company_id: int
    product_id: int
    warehouse_id: int
    quantity: Decimal

    batch_number: str | None = None
    serial_number: str | None = None

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "Company ID must be greater than zero"
            )

        if self.product_id <= 0:
            raise ValueError(
                "Product ID must be greater than zero"
            )

        if self.warehouse_id <= 0:
            raise ValueError(
                "Warehouse ID must be greater than zero"
            )

        if self.quantity < 0:
            raise ValueError(
                "Inventory tracking balance "
                "cannot be negative"
            )

        if (
            self.batch_number is not None
            and not self.batch_number.strip()
        ):
            raise ValueError(
                "Batch number cannot be empty when provided"
            )

        if (
            self.serial_number is not None
            and not self.serial_number.strip()
        ):
            raise ValueError(
                "Serial number cannot be empty when provided"
            )

        if (
            self.serial_number is not None
            and self.quantity > Decimal("1")
        ):
            raise ValueError(
                "Serial inventory balance "
                "cannot exceed 1"
            )

    @property
    def has_batch(self) -> bool:
        return self.batch_number is not None

    @property
    def has_serial(self) -> bool:
        return self.serial_number is not None

    @property
    def is_available(self) -> bool:
        return self.quantity > 0