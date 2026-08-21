from enum import StrEnum


class InventoryTrackingMovementType(StrEnum):
    """
    Direction of an inventory tracking movement.

    INCREASE
        Increases tracked inventory quantity.

    DECREASE
        Decreases tracked inventory quantity.
    """

    INCREASE = "increase"
    DECREASE = "decrease"