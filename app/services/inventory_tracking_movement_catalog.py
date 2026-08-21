from decimal import Decimal

from app.services.inventory_tracking_movement_definition import (
    InventoryTrackingMovementDefinition,
)


class InventoryTrackingMovementCatalog:
    def __init__(
        self,
        movements: tuple[
            InventoryTrackingMovementDefinition,
            ...,
        ],
    ) -> None:
        self._movements = tuple(movements)

    def for_stock(
        self,
        company_id: int,
        product_id: int,
        warehouse_id: int,
    ) -> tuple[
        InventoryTrackingMovementDefinition,
        ...,
    ]:
        return tuple(
            movement
            for movement in self._movements
            if (
                movement.company_id == company_id
                and movement.product_id == product_id
                and movement.warehouse_id == warehouse_id
            )
        )

    def for_batch(
        self,
        company_id: int,
        product_id: int,
        warehouse_id: int,
        batch_number: str,
    ) -> tuple[
        InventoryTrackingMovementDefinition,
        ...,
    ]:
        return tuple(
            movement
            for movement in self.for_stock(
                company_id=company_id,
                product_id=product_id,
                warehouse_id=warehouse_id,
            )
            if movement.batch_number == batch_number
        )

    def for_serial(
        self,
        company_id: int,
        product_id: int,
        serial_number: str,
        warehouse_id: int | None = None,
    ) -> tuple[
        InventoryTrackingMovementDefinition,
        ...,
    ]:
        return tuple(
            movement
            for movement in self._movements
            if (
                movement.company_id == company_id
                and movement.product_id == product_id
                and movement.serial_number == serial_number
                and (
                    warehouse_id is None
                    or movement.warehouse_id == warehouse_id
                )
            )
        )

    def stock_quantity(
        self,
        company_id: int,
        product_id: int,
        warehouse_id: int,
    ) -> Decimal:
        return sum(
            (
                movement.signed_quantity
                for movement in self.for_stock(
                    company_id=company_id,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            ),
            start=Decimal("0"),
        )

    def batch_quantity(
        self,
        company_id: int,
        product_id: int,
        warehouse_id: int,
        batch_number: str,
    ) -> Decimal:
        return sum(
            (
                movement.signed_quantity
                for movement in self.for_batch(
                    company_id=company_id,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                    batch_number=batch_number,
                )
            ),
            start=Decimal("0"),
        )

    def serial_quantity(
        self,
        company_id: int,
        product_id: int,
        serial_number: str,
        warehouse_id: int | None = None,
    ) -> Decimal:
        return sum(
            (
                movement.signed_quantity
                for movement in self.for_serial(
                    company_id=company_id,
                    product_id=product_id,
                    serial_number=serial_number,
                    warehouse_id=warehouse_id,
                )
            ),
            start=Decimal("0"),
        )

    def all(
        self,
    ) -> tuple[
        InventoryTrackingMovementDefinition,
        ...,
    ]:
        return self._movements