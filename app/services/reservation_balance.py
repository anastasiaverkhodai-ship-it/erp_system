from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ReservationBalance:
    """
    Reservation balance for a product in a warehouse.

    available_quantity is always calculated as:

        physical_quantity - reserved_quantity
    """

    company_id: int
    product_id: int
    warehouse_id: int
    physical_quantity: Decimal
    reserved_quantity: Decimal

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

        if self.physical_quantity < 0:
            raise ValueError(
                "Physical quantity cannot be negative"
            )

        if self.reserved_quantity < 0:
            raise ValueError(
                "Reserved quantity cannot be negative"
            )

        if self.reserved_quantity > self.physical_quantity:
            raise ValueError(
                "Reserved quantity cannot exceed "
                "physical quantity"
            )

    @property
    def available_quantity(self) -> Decimal:
        return (
            self.physical_quantity
            - self.reserved_quantity
        )