from app.services.posting_engine import PostingEngine
from app.services.posting_registry import (
    get_default_posting_handlers,
)


def create_default_posting_engine() -> PostingEngine:
    return PostingEngine(
        handlers=get_default_posting_handlers(),
    )