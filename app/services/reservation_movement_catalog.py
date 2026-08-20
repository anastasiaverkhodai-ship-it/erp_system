from decimal import Decimal

from app.services.reservation_movement_definition import (
    ReservationMovementDefinition,
)


class ReservationMovementCatalog:
    def __init__(
        self,
        movements: tuple[
            ReservationMovementDefinition,
            ...,
        ],
    ) -> None:
        self._movements = tuple(movements)

    def for_stock(
        self,
        company_id: int,
        product_id: int,
        warehouse_id: int,
    ) -> tuple[ReservationMovementDefinition, ...]:
        return tuple(
            movement
            for movement in self._movements
            if (
                movement.company_id == company_id
                and movement.product_id == product_id
                and movement.warehouse_id == warehouse_id
            )
        )

    def for_source_line(
        self,
        company_id: int,
        source_document_line_id: int,
    ) -> tuple[ReservationMovementDefinition, ...]:
        return tuple(
            movement
            for movement in self._movements
            if (
                movement.company_id == company_id
                and movement.source_document_line_id
                == source_document_line_id
            )
        )

    def reserved_quantity(
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

    def reserved_quantity_for_source_line(
        self,
        company_id: int,
        source_document_line_id: int,
    ) -> Decimal:
        return sum(
            (
                movement.signed_quantity
                for movement in self.for_source_line(
                    company_id=company_id,
                    source_document_line_id=source_document_line_id,
                )
            ),
            start=Decimal("0"),
        )

    def all(
        self,
    ) -> tuple[ReservationMovementDefinition, ...]:
        return self._movements