from enum import StrEnum


class AccountingAccountRole(StrEnum):
    INVENTORY_GOODS = "inventory_goods"

    BANK_CURRENT_UAH = "bank_current_uah"

    CUSTOMER_RECEIVABLES = "customer_receivables"
    SUPPLIER_ADVANCES = "supplier_advances"

    SUPPLIER_PAYABLES = "supplier_payables"

    TAX_SETTLEMENT = "tax_settlement"
    VAT_OUTPUT = "vat_output"
    VAT_INPUT = "vat_input"

    CUSTOMER_ADVANCES = "customer_advances"

    GOODS_REVENUE = "goods_revenue"
    SALES_DEDUCTIONS = "sales_deductions"

    GOODS_COGS = "goods_cogs"
