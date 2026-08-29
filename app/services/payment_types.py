from enum import StrEnum


class PaymentDirection(StrEnum):
    """
    Direction of money movement from the company's perspective.

    INCOMING
        Money received from a counterparty.

    OUTGOING
        Money paid to a counterparty.
    """

    INCOMING = "incoming"
    OUTGOING = "outgoing"


class PaymentStatus(StrEnum):
    """
    Commercial lifecycle of a Payment.

    STEP 16A deliberately does not imply GL posting.
    """

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class PaymentSettlementAllocationStatus(StrEnum):
    """
    Lifecycle of one persistent Payment <-> Open Item allocation.
    """

    ACTIVE = "active"
    REVERSED = "reversed"
