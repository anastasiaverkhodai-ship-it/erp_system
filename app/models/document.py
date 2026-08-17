from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.document_line import DocumentLine


class DocumentType(str, Enum):
    RECEIPT = "receipt"
    ISSUE = "issue"
    ADJUSTMENT = "adjustment"


class DocumentStatus(str, Enum):
    DRAFT = "draft"
    POSTED = "posted"
    REVERSED = "reversed"
    CANCELLED = "cancelled"


class Document(Base):
    __tablename__ = "documents"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "number",
            name="uq_document_company_number",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    number: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    document_type: Mapped[DocumentType] = mapped_column(
    SQLEnum(
        DocumentType,
        name="document_type_enum",
        native_enum=False,
        values_callable=lambda enum: [item.value for item in enum],
    ),
    nullable=False,
)

    document_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    status: Mapped[DocumentStatus] = mapped_column(
    SQLEnum(
        DocumentStatus,
        name="document_status_enum",
        native_enum=False,
        values_callable=lambda enum: [item.value for item in enum],
    ),
    default=DocumentStatus.DRAFT,
    nullable=False,
)

    created_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    reversed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    reversed_by: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    lines: Mapped[list["DocumentLine"]] = relationship(
        "DocumentLine",
        back_populates="document",
        cascade="all, delete-orphan",
    )