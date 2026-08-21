from app.services.inventory_tracking_movement_catalog import (
    InventoryTrackingMovementCatalog,
)
from app.services.inventory_tracking_movement_definition import (
    InventoryTrackingMovementDefinition,
)


class InventoryTrackingMovementValidationError(Exception):
    """Base error for inventory tracking movement validation."""


class NegativeBatchTrackingBalanceError(
    InventoryTrackingMovementValidationError
):
    """Raised when a batch balance would become negative."""


class NegativeSerialTrackingBalanceError(
    InventoryTrackingMovementValidationError
):
    """Raised when a serial balance would become negative."""


class SerialTrackingBalanceExceededError(
    InventoryTrackingMovementValidationError
):
    """
    Raised when a serial number would exist
    in inventory more than once.
    """


class NegativeSerialWarehouseBalanceError(
    InventoryTrackingMovementValidationError
):
    """
    Raised when a serial is removed from a warehouse
    where it is not currently available.
    """


def validate_new_inventory_tracking_movement(
    movement: InventoryTrackingMovementDefinition,
    catalog: InventoryTrackingMovementCatalog,
) -> None:
    """
    Validate a new tracking movement against
    the existing movement history.
    """

    if movement.batch_number is not None:
        current_batch_quantity = catalog.batch_quantity(
            company_id=movement.company_id,
            product_id=movement.product_id,
            warehouse_id=movement.warehouse_id,
            batch_number=movement.batch_number,
        )

        resulting_batch_quantity = (
            current_batch_quantity
            + movement.signed_quantity
        )

        if resulting_batch_quantity < 0:
            raise NegativeBatchTrackingBalanceError(
                "Batch inventory balance cannot become negative: "
                f"company_id={movement.company_id}, "
                f"product_id={movement.product_id}, "
                f"warehouse_id={movement.warehouse_id}, "
                f"batch_number='{movement.batch_number}', "
                f"current={current_batch_quantity}, "
                f"movement={movement.signed_quantity}, "
                f"result={resulting_batch_quantity}"
            )

    if movement.serial_number is not None:
        current_serial_quantity = catalog.serial_quantity(
            company_id=movement.company_id,
            product_id=movement.product_id,
            serial_number=movement.serial_number,
        )

        resulting_serial_quantity = (
            current_serial_quantity
            + movement.signed_quantity
        )

        if resulting_serial_quantity < 0:
            raise NegativeSerialTrackingBalanceError(
                "Serial inventory balance cannot become negative: "
                f"serial_number='{movement.serial_number}', "
                f"current={current_serial_quantity}, "
                f"movement={movement.signed_quantity}, "
                f"result={resulting_serial_quantity}"
            )

        if resulting_serial_quantity > 1:
            raise SerialTrackingBalanceExceededError(
                "Serial inventory balance cannot exceed 1: "
                f"serial_number='{movement.serial_number}', "
                f"current={current_serial_quantity}, "
                f"movement={movement.signed_quantity}, "
                f"result={resulting_serial_quantity}"
            )

        current_warehouse_quantity = catalog.serial_quantity(
            company_id=movement.company_id,
            product_id=movement.product_id,
            serial_number=movement.serial_number,
            warehouse_id=movement.warehouse_id,
        )

        resulting_warehouse_quantity = (
            current_warehouse_quantity
            + movement.signed_quantity
        )

        if resulting_warehouse_quantity < 0:
            raise NegativeSerialWarehouseBalanceError(
                "Serial cannot be removed from a warehouse "
                "where it is not available: "
                f"serial_number='{movement.serial_number}', "
                f"warehouse_id={movement.warehouse_id}, "
                f"current={current_warehouse_quantity}, "
                f"movement={movement.signed_quantity}"
            )