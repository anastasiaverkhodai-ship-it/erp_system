from typing import Protocol

from app.services.reversal_context import ReversalContext


class ReversalHandlerError(Exception):
    """Base business error raised by a reversal handler."""


class ReversalHandler(Protocol):
    async def reverse(
        self,
        context: ReversalContext,
    ) -> None:
        ...