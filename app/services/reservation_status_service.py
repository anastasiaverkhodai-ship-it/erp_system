from decimal import Decimal

from app.services.reservation_movement_catalog import (
    ReservationMovementCatalog,
)
from app.services.reservation_status_types import (
    ReservationStatus,
)


class ReservationStatusError(Exception):
    """Base error for reservation status calculations."""


class ReservationOverReservedError(
    ReservationStatusError
):
    """Raised when reserved quantity exceeds source quantity."""


def calculate_reservation_status(
    company_id: int,
    source_document_line_id: int,
    source_quantity: Decimal,
    catalog: ReservationMovementCatalog,
) -> ReservationStatus:
    """
    Determine reservation status of a source document line.
    """

    if company_id <= 0:
        raise ValueError(
            "Company ID must be greater than zero"
        )

    if source_document_line_id <= 0:
        raise ValueError(
            "Source document line ID must be greater than zero"
        )

    if source_quantity <= 0:
        raise ValueError(
            "Source quantity must be greater than zero"
        )

    reserved_quantity = (
        catalog.reserved_quantity_for_source_line(
            company_id=company_id,
            source_document_line_id=source_document_line_id,
        )
    )

    if reserved_quantity < 0:
        raise ReservationStatusError(
            "Reserved quantity cannot be negative: "
            f"company_id={company_id}, "
            f"source_document_line_id="
            f"{source_document_line_id}, "
            f"reserved_quantity={reserved_quantity}"
        )

    if reserved_quantity > source_quantity:
        raise ReservationOverReservedError(
            "Reserved quantity exceeds source quantity: "
            f"source_line_id={source_document_line_id}, "
            f"source_quantity={source_quantity}, "
            f"reserved_quantity={reserved_quantity}"
        )

    if reserved_quantity == 0:
        return ReservationStatus.NOT_RESERVED

    if reserved_quantity == source_quantity:
        return ReservationStatus.FULLY_RESERVED

    return ReservationStatus.PARTIALLY_RESERVED