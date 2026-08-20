from collections.abc import Sequence

from app.services.reversal_context import ReversalContext
from app.services.reversal_handler import (
    ReversalHandler,
    ReversalHandlerError,
)


class ReversalEngineError(Exception):
    """Business error raised by the reversal engine."""


class ReversalEngine:
    def __init__(
        self,
        handlers: Sequence[ReversalHandler],
    ) -> None:
        self.handlers = tuple(handlers)

    async def reverse(
        self,
        context: ReversalContext,
    ) -> None:
        for handler in self.handlers:
            try:
                await handler.reverse(
                    context
                )
            except ReversalHandlerError as exc:
                raise ReversalEngineError(
                    str(exc)
                ) from exc