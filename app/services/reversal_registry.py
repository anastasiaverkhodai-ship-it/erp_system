from app.services.accounting_reversal_handler import (
    AccountingReversalHandler,
)
from app.services.reversal_handler import ReversalHandler
from app.services.warehouse_reversal_handler import (
    WarehouseReversalHandler,
)


def get_default_reversal_handlers(
) -> tuple[ReversalHandler, ...]:
    return (
        WarehouseReversalHandler(),
        AccountingReversalHandler(),
    )