from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.inventory_tracking_movement_types import (
    InventoryTrackingMovementType,
)


@dataclass(frozen=True, slots=True)
class InventoryTrackingMovementDefinition:
    """
    Inventory movement identified by batch and/or serial.

    quantity
        Quantity in the product's inventory/base UOM.

    batch_number
        Optional batch / lot identifier.

    serial_number
        Optional individual serial number.

        When a serial number is present, quantity must
        always equal exactly 1.
    """

    company_id: int
    product_id: int
    warehouse_id: int

    source_document_id: int
    source_document_line_id: int

    movement_date: date
    movement_type: InventoryTrackingMovementType
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
                "Inventory tracking movement quantity "
                "must be greater than zero"
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
            and self.quantity != Decimal("1")
        ):
            raise ValueError(
                "Serial-numbered inventory movement "
                "quantity must equal 1"
            )

    @property
    def signed_quantity(self) -> Decimal:
        if (
            self.movement_type
            == InventoryTrackingMovementType.INCREASE
        ):
            return self.quantity

        return -self.quantity