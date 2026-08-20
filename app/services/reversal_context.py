from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.journal_entry import JournalEntry
from app.models.stock_ledger import StockLedger


@dataclass(slots=True)
class ReversalContext:
    db: AsyncSession
    document: Document
    reversal_date: date
    reversal_time: datetime
    reversed_by: int
    original_stock_movements: list[StockLedger] = field(
        default_factory=list
    )
    original_journal_entry: JournalEntry | None = None

    @property
    def company_id(self) -> int:
        return self.document.company_id

    @property
    def document_id(self) -> int:
        return self.document.id


def create_reversal_context(
    db: AsyncSession,
    document: Document,
    reversal_date: date,
    reversed_by: int,
) -> ReversalContext:
    return ReversalContext(
        db=db,
        document=document,
        reversal_date=reversal_date,
        reversal_time=datetime.now(
            timezone.utc
        ).replace(tzinfo=None),
        reversed_by=reversed_by,
    )