from app.models.inventory_cost_entry import InventoryCostEntry
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.moving_average_movement import MovingAverageMovement
from app.models.stock_ledger import StockLedger
from app.services.register_binding import RegisterBinding
from app.services.register_catalog import (
    ACCOUNTING_ENTRIES,
    INVENTORY_COST_MOVEMENTS,
    STOCK_MOVEMENTS,
)


STOCK_MOVEMENTS_BINDING = RegisterBinding(
    definition=STOCK_MOVEMENTS,
    persistence_models=(
        StockLedger,
    ),
)

INVENTORY_COST_MOVEMENTS_BINDING = RegisterBinding(
    definition=INVENTORY_COST_MOVEMENTS,
    persistence_models=(
        InventoryCostEntry,
        MovingAverageMovement,
    ),
)

ACCOUNTING_ENTRIES_BINDING = RegisterBinding(
    definition=ACCOUNTING_ENTRIES,
    persistence_models=(
        JournalEntry,
        JournalEntryLine,
    ),
)


SYSTEM_REGISTER_BINDINGS: tuple[RegisterBinding, ...] = (
    STOCK_MOVEMENTS_BINDING,
    INVENTORY_COST_MOVEMENTS_BINDING,
    ACCOUNTING_ENTRIES_BINDING,
)