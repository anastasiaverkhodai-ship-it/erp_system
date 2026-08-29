from enum import StrEnum


class InvoiceFulfillmentAllocationStatus(StrEnum):
    """
    Lifecycle of one persistent Invoice <-> Fulfillment allocation.

    ACTIVE
        Quantity currently participates in invoice/fulfillment
        reconciliation.

    REVERSED
        Allocation was explicitly undone and remains stored for
        audit history.
    """

    ACTIVE = "active"
    REVERSED = "reversed"
