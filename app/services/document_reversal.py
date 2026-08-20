from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentStatus,
)
from app.models.journal_entry import JournalEntry
from app.models.stock_ledger import (
    StockLedger,
    StockMovementType,
)
from app.services.accounting_period_service import (
    ensure_period_open,
)
from app.services.reversal_context import (
    create_reversal_context,
)
from app.services.reversal_engine import (
    ReversalEngineError,
)
from app.services.reversal_factory import (
    create_default_reversal_engine,
)


class DocumentReversalError(Exception):
    """Business error raised when a document cannot be reversed."""


class DocumentReversalNotFoundError(
    DocumentReversalError
):
    """Raised when the requested document does not exist."""


async def reverse_document(
    db: AsyncSession,
    company_id: int,
    document_id: int,
    reversal_date: date,
    reversed_by: int,
) -> Document:
    # ---------------------------------------------------------
    # LOCK DOCUMENT
    # ---------------------------------------------------------

    result = await db.execute(
        select(Document)
        .where(
            Document.id == document_id,
            Document.company_id == company_id,
        )
        .with_for_update()
    )

    document = result.scalar_one_or_none()

    if document is None:
        raise DocumentReversalNotFoundError(
            "Document not found"
        )

    if document.status != DocumentStatus.POSTED:
        raise DocumentReversalError(
            "Only posted documents can be reversed"
        )

    # ---------------------------------------------------------
    # CHECK REVERSAL ACCOUNTING PERIOD
    # ---------------------------------------------------------

    await ensure_period_open(
        company_id=document.company_id,
        operation_date=reversal_date,
        db=db,
    )

    # ---------------------------------------------------------
    # LOAD ORIGINAL STOCK MOVEMENTS
    # ---------------------------------------------------------

    movements_result = await db.execute(
        select(StockLedger)
        .where(
            StockLedger.company_id == company_id,
            StockLedger.document_id == document.id,
            StockLedger.movement_type
            != StockMovementType.REVERSAL,
        )
        .order_by(StockLedger.id)
    )

    original_movements = list(
        movements_result.scalars().all()
    )

    if not original_movements:
        raise DocumentReversalError(
            "Document has no stock movements to reverse"
        )

    # ---------------------------------------------------------
    # LOAD ORIGINAL ACCOUNTING JOURNAL ENTRY
    # ---------------------------------------------------------

    journal_result = await db.execute(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.document_id == document.id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )

    original_journal_entry = (
        journal_result.scalar_one_or_none()
    )

    # ---------------------------------------------------------
    # CREATE REVERSAL CONTEXT
    # ---------------------------------------------------------

    context = create_reversal_context(
        db=db,
        document=document,
        reversal_date=reversal_date,
        reversed_by=reversed_by,
    )

    context.original_stock_movements = (
        original_movements
    )
    context.original_journal_entry = (
        original_journal_entry
    )

    # ---------------------------------------------------------
    # REVERSAL ENGINE
    # ---------------------------------------------------------

    reversal_engine = create_default_reversal_engine()

    try:
        await reversal_engine.reverse(
            context
        )
    except ReversalEngineError as exc:
        raise DocumentReversalError(
            str(exc)
        ) from exc

    # ---------------------------------------------------------
    # MARK DOCUMENT AS REVERSED
    # ---------------------------------------------------------

    document.status = DocumentStatus.REVERSED
    document.reversed_at = context.reversal_time
    document.reversed_by = context.reversed_by

    await db.flush()

    return document