from dataclasses import dataclass
from decimal import Decimal

from app.services.reservation_types import (
    ReservationMovementType,
)


@dataclass(frozen=True, slots=True)
class ReservationMovementDefinition:
    """
    Immutable reservation movement.

    company_id
        Company that owns the reservation.

    product_id
        Product being reserved.

    warehouse_id
        Warehouse where stock is reserved.

    source_document_id
        Business document responsible for the reservation,
        for example a sales order.

    source_document_line_id
        Specific source document line responsible
        for the reservation.

    quantity
        Positive quantity expressed in the product's
        inventory/base unit of measure.

    movement_type
        RESERVE, RELEASE, or CONSUME.
    """

    company_id: int
    product_id: int
    warehouse_id: int
    source_document_id: int
    source_document_line_id: int
    quantity: Decimal
    movement_type: ReservationMovementType

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

        if self.source_document_id <= 0:
            raise ValueError(
                "Source document ID must be greater than zero"
            )

        if self.source_document_line_id <= 0:
            raise ValueError(
                "Source document line ID must be greater than zero"
            )

        if self.quantity <= 0:
            raise ValueError(
                "Reservation movement quantity "
                "must be greater than zero"
            )

    @property
    def signed_quantity(self) -> Decimal:
        if self.movement_type == ReservationMovementType.RESERVE:
            return self.quantity

        return -self.quantity