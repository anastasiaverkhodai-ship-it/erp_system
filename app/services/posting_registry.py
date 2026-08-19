from app.services.accounting_posting_handler import (
    AccountingPostingHandler,
)
from app.services.posting_handler import PostingHandler
from app.services.warehouse_posting_handler import (
    WarehousePostingHandler,
)


def get_default_posting_handlers(
) -> tuple[PostingHandler, ...]:
    return (
        WarehousePostingHandler(),
        AccountingPostingHandler(),
    )