from enum import StrEnum


class TradeDirection(StrEnum):
    SALE = "sale"
    PURCHASE = "purchase"


class TradeDocumentKind(StrEnum):
    ORDER = "order"
    INVOICE = "invoice"


class TradeDocumentStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
