from dataclasses import dataclass

from app.services.inventory_tracking_types import (
    InventoryTrackingMode,
)


@dataclass(frozen=True, slots=True)
class ProductTrackingPolicyDefinition:
    """
    Inventory tracking policy for a company-specific product.
    """

    company_id: int
    product_id: int
    tracking_mode: InventoryTrackingMode

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "Company ID must be greater than zero"
            )

        if self.product_id <= 0:
            raise ValueError(
                "Product ID must be greater than zero"
            )

    @property
    def tracks_batches(self) -> bool:
        return self.tracking_mode in {
            InventoryTrackingMode.BATCH,
            InventoryTrackingMode.BATCH_AND_SERIAL,
        }

    @property
    def tracks_serials(self) -> bool:
        return self.tracking_mode in {
            InventoryTrackingMode.SERIAL,
            InventoryTrackingMode.BATCH_AND_SERIAL,
        }