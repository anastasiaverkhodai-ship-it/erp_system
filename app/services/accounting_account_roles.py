from enum import StrEnum


class AccountingAccountRole(StrEnum):
    PAYROLL_EXPENSE = "payroll_expense"
    PAYROLL_EMPLOYER_CONTRIBUTION_EXPENSE = "payroll_employer_contribution_expense"
    PAYROLL_NET_PAYABLE = "payroll_net_payable"
    PAYROLL_DEDUCTION_PAYABLE = "payroll_deduction_payable"
    PAYROLL_PIT_PAYABLE = "payroll_pit_payable"
    PAYROLL_MILITARY_LEVY_PAYABLE = "payroll_military_levy_payable"
    PAYROLL_USC_PAYABLE = "payroll_usc_payable"

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
