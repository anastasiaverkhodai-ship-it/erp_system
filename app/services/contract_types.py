from enum import StrEnum


class ContractType(StrEnum):
    SALES = "sales"
    PURCHASE = "purchase"
    MIXED = "mixed"


class ContractStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"
