from enum import StrEnum


class RecalculationStatus(StrEnum):
    """
    Lifecycle status of one backdated
    recalculation request.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class RecalculationDomain(StrEnum):
    """
    ERP domain affected by a backdated change.

    The foundation starts with inventory and
    accounting. Additional domains may be added
    later without changing the request lifecycle.
    """

    INVENTORY = "inventory"
    ACCOUNTING = "accounting"
