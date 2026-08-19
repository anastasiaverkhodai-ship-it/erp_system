from enum import StrEnum


class PostingContextKey(StrEnum):
    STOCK_DELTAS = "stock_deltas"
    INVENTORY_COSTS = "inventory_costs"
    JOURNAL_ENTRY = "journal_entry"