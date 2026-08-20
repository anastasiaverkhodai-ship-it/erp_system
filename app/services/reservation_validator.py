from decimal import Decimal

from app.services.reservation_movement_definition import (
    ReservationMovementDefinition,
)


class ReservationValidationError(Exception):
    """Base error for reservation movement validation."""


class NegativeReservationStockError(
    ReservationValidationError
):
    """Raised when total reserved stock would become negative."""


class NegativeReservationSourceLineError(
    ReservationValidationError
):
    """Raised when a source line reservation would become negative."""


def validate_reservation_movements(
    movements: tuple[
        ReservationMovementDefinition,
        ...,
    ],
) -> None:
    """
    Validate reservation movements in their business order.

    Neither the total reservation for product + warehouse
    nor the reservation of a specific source document line
    may become negative.
    """

    stock_balances: dict[
        tuple[int, int, int],
        Decimal,
    ] = {}

    source_line_balances: dict[
        tuple[int, int],
        Decimal,
    ] = {}

    for movement in movements:
        stock_key = (
            movement.company_id,
            movement.product_id,
            movement.warehouse_id,
        )

        source_line_key = (
            movement.company_id,
            movement.source_document_line_id,
        )

        stock_balance = (
            stock_balances.get(
                stock_key,
                Decimal("0"),
            )
            + movement.signed_quantity
        )

        source_line_balance = (
            source_line_balances.get(
                source_line_key,
                Decimal("0"),
            )
            + movement.signed_quantity
        )

        if stock_balance < 0:
            raise NegativeReservationStockError(
                "Reserved stock cannot become negative: "
                f"company_id={movement.company_id}, "
                f"product_id={movement.product_id}, "
                f"warehouse_id={movement.warehouse_id}, "
                f"balance={stock_balance}"
            )

        if source_line_balance < 0:
            raise NegativeReservationSourceLineError(
                "Source line reservation cannot become negative: "
                f"company_id={movement.company_id}, "
                f"source_document_line_id="
                f"{movement.source_document_line_id}, "
                f"balance={source_line_balance}"
            )

        stock_balances[stock_key] = stock_balance
        source_line_balances[
            source_line_key
        ] = source_line_balance