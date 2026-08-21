from app.services.batch_catalog import (
    BatchCatalog,
)
from app.services.inventory_tracking_types import (
    InventoryTrackingMode,
)
from app.services.product_tracking_policy_catalog import (
    ProductTrackingPolicyCatalog,
)
from app.services.serial_catalog import (
    SerialCatalog,
)


class InventoryTrackingValidationError(Exception):
    """Base error for inventory tracking validation."""


class BatchNotAllowedError(
    InventoryTrackingValidationError
):
    """Raised when a batch is supplied but not allowed."""


class BatchRequiredError(
    InventoryTrackingValidationError
):
    """Raised when a batch is required but missing."""


class SerialNotAllowedError(
    InventoryTrackingValidationError
):
    """Raised when a serial is supplied but not allowed."""


class SerialRequiredError(
    InventoryTrackingValidationError
):
    """Raised when a serial number is required but missing."""


class SerialBatchMismatchError(
    InventoryTrackingValidationError
):
    """
    Raised when a serial belongs to a different batch.
    """


def validate_inventory_tracking(
    company_id: int,
    product_id: int,
    batch_number: str | None,
    serial_number: str | None,
    policy_catalog: ProductTrackingPolicyCatalog,
    batch_catalog: BatchCatalog,
    serial_catalog: SerialCatalog,
) -> None:
    """
    Validate batch / serial identification according
    to the product tracking policy.
    """

    policy = policy_catalog.get(
        company_id=company_id,
        product_id=product_id,
    )

    mode = policy.tracking_mode

    if mode == InventoryTrackingMode.NONE:
        if batch_number is not None:
            raise BatchNotAllowedError(
                "Batch is not allowed for a product "
                "with NONE tracking mode"
            )

        if serial_number is not None:
            raise SerialNotAllowedError(
                "Serial number is not allowed for a product "
                "with NONE tracking mode"
            )

        return

    if mode == InventoryTrackingMode.BATCH:
        if batch_number is None:
            raise BatchRequiredError(
                "Batch is required for a product "
                "with BATCH tracking mode"
            )

        if serial_number is not None:
            raise SerialNotAllowedError(
                "Serial number is not allowed for a product "
                "with BATCH tracking mode"
            )

        batch_catalog.get(
            company_id=company_id,
            product_id=product_id,
            batch_number=batch_number,
        )

        return

    if mode == InventoryTrackingMode.SERIAL:
        if batch_number is not None:
            raise BatchNotAllowedError(
                "Batch is not allowed for a product "
                "with SERIAL tracking mode"
            )

        if serial_number is None:
            raise SerialRequiredError(
                "Serial number is required for a product "
                "with SERIAL tracking mode"
            )

        serial = serial_catalog.get(
            company_id=company_id,
            product_id=product_id,
            serial_number=serial_number,
        )

        if serial.batch_number is not None:
            raise SerialBatchMismatchError(
                "Serial number is associated with a batch "
                "but product tracking mode is SERIAL"
            )

        return

    if mode == InventoryTrackingMode.BATCH_AND_SERIAL:
        if batch_number is None:
            raise BatchRequiredError(
                "Batch is required for a product "
                "with BATCH_AND_SERIAL tracking mode"
            )

        if serial_number is None:
            raise SerialRequiredError(
                "Serial number is required for a product "
                "with BATCH_AND_SERIAL tracking mode"
            )

        batch_catalog.get(
            company_id=company_id,
            product_id=product_id,
            batch_number=batch_number,
        )

        serial = serial_catalog.get(
            company_id=company_id,
            product_id=product_id,
            serial_number=serial_number,
        )

        if serial.batch_number != batch_number:
            raise SerialBatchMismatchError(
                "Serial number does not belong to "
                "the specified batch: "
                f"serial_number='{serial_number}', "
                f"expected_batch='{batch_number}', "
                f"actual_batch='{serial.batch_number}'"
            )

        return

    raise InventoryTrackingValidationError(
        f"Unsupported inventory tracking mode: {mode}"
    )