from decimal import Decimal

from app.services.inventory_tracking_balance import (
    InventoryTrackingBalance,
)
from app.services.inventory_tracking_movement_catalog import (
    InventoryTrackingMovementCatalog,
)


def build_inventory_tracking_balance(
    company_id: int,
    product_id: int,
    warehouse_id: int,
    catalog: InventoryTrackingMovementCatalog,
    batch_number: str | None = None,
    serial_number: str | None = None,
) -> InventoryTrackingBalance:
    """
    Build an inventory tracking balance from
    tracking movements.

    Supported scopes:

    - product + warehouse
    - product + warehouse + batch
    - product + warehouse + serial
    - product + warehouse + batch + serial
    """

    if company_id <= 0:
        raise ValueError(
            "Company ID must be greater than zero"
        )

    if product_id <= 0:
        raise ValueError(
            "Product ID must be greater than zero"
        )

    if warehouse_id <= 0:
        raise ValueError(
            "Warehouse ID must be greater than zero"
        )

    if (
        batch_number is not None
        and not batch_number.strip()
    ):
        raise ValueError(
            "Batch number cannot be empty when provided"
        )

    if (
        serial_number is not None
        and not serial_number.strip()
    ):
        raise ValueError(
            "Serial number cannot be empty when provided"
        )

    if (
        batch_number is None
        and serial_number is None
    ):
        quantity = catalog.stock_quantity(
            company_id=company_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
        )

    elif (
        batch_number is not None
        and serial_number is None
    ):
        quantity = catalog.batch_quantity(
            company_id=company_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            batch_number=batch_number,
        )

    elif (
        batch_number is None
        and serial_number is not None
    ):
        quantity = catalog.serial_quantity(
            company_id=company_id,
            product_id=product_id,
            serial_number=serial_number,
            warehouse_id=warehouse_id,
        )

    else:
        quantity = sum(
            (
                movement.signed_quantity
                for movement in catalog.for_serial(
                    company_id=company_id,
                    product_id=product_id,
                    serial_number=serial_number,
                    warehouse_id=warehouse_id,
                )
                if movement.batch_number == batch_number
            ),
            start=Decimal("0"),
        )

    return InventoryTrackingBalance(
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity=quantity,
        batch_number=batch_number,
        serial_number=serial_number,
    )