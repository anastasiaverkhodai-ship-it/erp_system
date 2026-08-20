from enum import StrEnum


class ReservationStatus(StrEnum):
    """
    Reservation state of a source document line.
    """

    NOT_RESERVED = "not_reserved"
    PARTIALLY_RESERVED = "partially_reserved"
    FULLY_RESERVED = "fully_reserved"