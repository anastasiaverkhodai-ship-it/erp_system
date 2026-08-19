from typing import Protocol

from app.services.posting_context import PostingContext


class PostingHandler(Protocol):
    async def post(
        self,
        context: PostingContext,
    ) -> None:
        ...