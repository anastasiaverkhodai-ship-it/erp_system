from enum import StrEnum


class AccountingDimensionType(StrEnum):
    """
    High-level accounting analytics dimensions.

    COUNTERPARTY
        Customer, supplier, or another business partner.

    PRODUCT
        Product or inventory item.

    WAREHOUSE
        Warehouse or storage location.

    DEPARTMENT
        Organizational department.

    COST_CENTER
        Cost center used for management/accounting analysis.

    PROJECT
        Project or activity.

    EMPLOYEE
        Employee-related accounting analytics.

    CONTRACT
        Contract or agreement with a counterparty.

    DOCUMENT
        Related business document.

    OTHER
        Custom or future analytical dimension.
    """

    COUNTERPARTY = "counterparty"
    PRODUCT = "product"
    WAREHOUSE = "warehouse"
    DEPARTMENT = "department"
    COST_CENTER = "cost_center"
    PROJECT = "project"
    EMPLOYEE = "employee"
    CONTRACT = "contract"
    DOCUMENT = "document"
    OTHER = "other"