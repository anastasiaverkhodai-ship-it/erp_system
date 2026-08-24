from enum import StrEnum


class CounterpartyType(StrEnum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    BOTH = "both"


class CounterpartyVatStatus(StrEnum):
    UNKNOWN = "unknown"
    NON_VAT_PAYER = "non_vat_payer"
    VAT_PAYER = "vat_payer"
