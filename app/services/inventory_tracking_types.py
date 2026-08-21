from enum import StrEnum


class InventoryTrackingMode(StrEnum):
    """
    Inventory identification mode for a product.

    NONE
        Product is tracked only by quantity.

    BATCH
        Product is tracked by batch / lot.

    SERIAL
        Every inventory unit has an individual
        serial number.

    BATCH_AND_SERIAL
        Serial-numbered units are additionally
        associated with a batch / lot.
    """

    NONE = "none"
    BATCH = "batch"
    SERIAL = "serial"
    BATCH_AND_SERIAL = "batch_and_serial"