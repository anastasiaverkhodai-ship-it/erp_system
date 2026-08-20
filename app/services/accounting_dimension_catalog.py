from app.services.accounting_dimension_definition import (
    AccountingDimensionDefinition,
)
from app.services.accounting_dimension_types import (
    AccountingDimensionType,
)


COUNTERPARTY = AccountingDimensionDefinition(
    code="counterparty",
    name="Counterparty",
    dimension_type=AccountingDimensionType.COUNTERPARTY,
)

PRODUCT = AccountingDimensionDefinition(
    code="product",
    name="Product",
    dimension_type=AccountingDimensionType.PRODUCT,
)

WAREHOUSE = AccountingDimensionDefinition(
    code="warehouse",
    name="Warehouse",
    dimension_type=AccountingDimensionType.WAREHOUSE,
)

DEPARTMENT = AccountingDimensionDefinition(
    code="department",
    name="Department",
    dimension_type=AccountingDimensionType.DEPARTMENT,
)

COST_CENTER = AccountingDimensionDefinition(
    code="cost_center",
    name="Cost Center",
    dimension_type=AccountingDimensionType.COST_CENTER,
)

PROJECT = AccountingDimensionDefinition(
    code="project",
    name="Project",
    dimension_type=AccountingDimensionType.PROJECT,
    required=False,
)

EMPLOYEE = AccountingDimensionDefinition(
    code="employee",
    name="Employee",
    dimension_type=AccountingDimensionType.EMPLOYEE,
)

CONTRACT = AccountingDimensionDefinition(
    code="contract",
    name="Contract",
    dimension_type=AccountingDimensionType.CONTRACT,
)

DOCUMENT = AccountingDimensionDefinition(
    code="document",
    name="Document",
    dimension_type=AccountingDimensionType.DOCUMENT,
)


SYSTEM_ACCOUNTING_DIMENSIONS: tuple[
    AccountingDimensionDefinition,
    ...,
] = (
    COUNTERPARTY,
    PRODUCT,
    WAREHOUSE,
    DEPARTMENT,
    COST_CENTER,
    PROJECT,
    EMPLOYEE,
    CONTRACT,
    DOCUMENT,
)