from decimal import Decimal

from app.services.reservation_movement_catalog import (
    ReservationMovementCatalog,
)


class ReservationAvailabilityError(Exception):
    """Base error for reservation availability calculations."""


class InvalidReservationBalanceError(
    ReservationAvailabilityError
):
    """Raised when calculated reserved quantity is negative."""


class InsufficientAvailableStockError(
    ReservationAvailabilityError
):
    """Raised when requested reservation exceeds available stock."""


def calculate_available_stock(
    physical_quantity: Decimal,
    company_id: int,
    product_id: int,
    warehouse_id: int,
    catalog: ReservationMovementCatalog,
) -> Decimal:
    """
    Calculate stock currently available for new reservations.

    available = physical stock - reserved stock
    """

    if physical_quantity < 0:
        raise ValueError(
            "Physical stock quantity cannot be negative"
        )

    reserved_quantity = catalog.reserved_quantity(
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
    )

    if reserved_quantity < 0:
        raise InvalidReservationBalanceError(
            "Reserved quantity cannot be negative: "
            f"company_id={company_id}, "
            f"product_id={product_id}, "
            f"warehouse_id={warehouse_id}, "
            f"reserved_quantity={reserved_quantity}"
        )

    return physical_quantity - reserved_quantity


def validate_new_reservation(
    requested_quantity: Decimal,
    physical_quantity: Decimal,
    company_id: int,
    product_id: int,
    warehouse_id: int,
    catalog: ReservationMovementCatalog,
) -> None:
    """
    Validate that enough stock is available
    for a new reservation.
    """

    if requested_quantity <= 0:
        raise ValueError(
            "Requested reservation quantity "
            "must be greater than zero"
        )

    available_quantity = calculate_available_stock(
        physical_quantity=physical_quantity,
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        catalog=catalog,
    )

    if requested_quantity > available_quantity:
        raise InsufficientAvailableStockError(
            "Insufficient available stock for reservation: "
            f"requested={requested_quantity}, "
            f"available={available_quantity}"
        )