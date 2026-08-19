from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentType


@dataclass(slots=True)
class PostingContext:
    db: AsyncSession
    document: Document
    operation_date: date
    posting_time: datetime

    @property
    def company_id(self) -> int:
        return self.document.company_id

    @property
    def document_id(self) -> int:
        return self.document.id

    @property
    def document_type(self) -> DocumentType:
        return self.document.document_type


def create_posting_context(
    db: AsyncSession,
    document: Document,
) -> PostingContext:
    return PostingContext(
        db=db,
        document=document,
        operation_date=document.document_date,
        posting_time=datetime.now(
            timezone.utc
        ).replace(tzinfo=None),
    )