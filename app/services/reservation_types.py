from enum import StrEnum


class ReservationMovementType(StrEnum):
    """
    High-level reservation movement types.

    RESERVE
        Increases reserved quantity.

    RELEASE
        Releases previously reserved quantity
        without shipping or consuming stock.

    CONSUME
        Uses reserved quantity during fulfillment,
        for example when goods are shipped.
    """

    RESERVE = "reserve"
    RELEASE = "release"
    CONSUME = "consume"