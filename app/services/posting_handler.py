from typing import Protocol

from app.services.posting_context import PostingContext


class PostingHandlerError(Exception):
    """Base business error raised by a posting handler."""


class PostingHandler(Protocol):
    async def post(
        self,
        context: PostingContext,
    ) -> None:
        ...