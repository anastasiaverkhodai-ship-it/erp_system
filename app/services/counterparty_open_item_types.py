from enum import StrEnum


class CounterpartyOpenItemType(StrEnum):
    RECEIVABLE = "receivable"
    PAYABLE = "payable"


class CounterpartyOpenItemStatus(StrEnum):
    OPEN = "open"
    PARTIALLY_SETTLED = "partially_settled"
    SETTLED = "settled"
    CANCELLED = "cancelled"
