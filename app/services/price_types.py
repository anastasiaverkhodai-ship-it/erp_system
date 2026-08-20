from enum import StrEnum


class PriceKind(StrEnum):
    """
    Business purpose of a price type.
    """

    SALES = "sales"
    PURCHASE = "purchase"
    INTERNAL = "internal"