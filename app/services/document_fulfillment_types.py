from enum import StrEnum


class FulfillmentStatus(StrEnum):
    """
    Fulfillment state of a source document line.
    """

    NOT_FULFILLED = "not_fulfilled"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FULFILLED = "fulfilled"