from app.services.posting_context import PostingContext
from app.services.warehouse_posting_handler import (
    WarehousePostingHandler,
    WarehousePostingHandlerError,
)


class PostingEngineError(Exception):
    """Business error raised by the posting engine."""


class PostingEngine:
    def __init__(self) -> None:
        self.warehouse_handler = WarehousePostingHandler()

    async def post_warehouse(
        self,
        context: PostingContext,
    ) -> None:
        try:
            await self.warehouse_handler.post(
                context
            )
        except WarehousePostingHandlerError as exc:
            raise PostingEngineError(
                str(exc)
            ) from exc