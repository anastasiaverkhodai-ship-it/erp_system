from enum import StrEnum


class PostingContextKey(StrEnum):
    STOCK_DELTAS = "stock_deltas"
    INVENTORY_COSTS = "inventory_costs"
    JOURNAL_ENTRY = "journal_entry"
    PVC_MA_ISSUE_RECONCILIATIONS = "pvc_ma_issue_reconciliations"
    RECEIPT_EXACT_VALUATION_AMOUNTS = (
        "receipt_exact_valuation_amounts"
    )
