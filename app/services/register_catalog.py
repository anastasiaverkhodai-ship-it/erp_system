from app.services.register_definition import RegisterDefinition
from app.services.register_types import RegisterKind


STOCK_MOVEMENTS = RegisterDefinition(
    code="stock_movements",
    kind=RegisterKind.ACCUMULATION,
    purpose="Warehouse stock quantity movements.",
)

INVENTORY_COST_MOVEMENTS = RegisterDefinition(
    code="inventory_cost_movements",
    kind=RegisterKind.ACCUMULATION,
    purpose="Inventory valuation and cost movements.",
)

ACCOUNTING_ENTRIES = RegisterDefinition(
    code="accounting_entries",
    kind=RegisterKind.ACCOUNTING,
    purpose="Double-entry financial accounting movements.",
)


SYSTEM_REGISTERS: tuple[RegisterDefinition, ...] = (
    STOCK_MOVEMENTS,
    INVENTORY_COST_MOVEMENTS,
    ACCOUNTING_ENTRIES,
)