from app.services.reversal_engine import ReversalEngine
from app.services.reversal_registry import (
    get_default_reversal_handlers,
)


def create_default_reversal_engine() -> ReversalEngine:
    return ReversalEngine(
        handlers=get_default_reversal_handlers(),
    )