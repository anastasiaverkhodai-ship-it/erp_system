from collections.abc import Sequence

from app.services.posting_context import PostingContext
from app.services.posting_handler import (
    PostingHandler,
    PostingHandlerError,
)


class PostingEngineError(Exception):
    """Business error raised by the posting engine."""


class PostingEngine:
    def __init__(
        self,
        handlers: Sequence[PostingHandler],
    ) -> None:
        self.handlers = tuple(handlers)

    async def post(
        self,
        context: PostingContext,
    ) -> None:
        for handler in self.handlers:
            try:
                await handler.post(
                    context
                )
            except PostingHandlerError as exc:
                raise PostingEngineError(
                    str(exc)
                ) from exc