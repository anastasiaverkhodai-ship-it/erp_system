from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class OpeningBalance(Base):
    """
    Minimal provenance/lifecycle identity for an opening-balance journal.

    Accounting amounts are intentionally NOT stored here.
    Debit/credit amounts live only in JournalEntryLine.
    """

    __tablename__ = "opening_balances"

    __table_args__ = (
        UniqueConstraint("company_id", "request_key", name="uq_opening_company_request"),
        ForeignKeyConstraint(["company_id","journal_entry_id"], ["journal_entries.company_id","journal_entries.id"], name="fk_opening_company_journal", ondelete="RESTRICT"),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_opening_balances_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "journal_entry_id",
            name="uq_opening_balances_company_journal",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    company_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    request_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    opening_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    journal_entry_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("journal_entries.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )

    created_by: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    journal_entry = relationship(
        "JournalEntry",
        foreign_keys=[journal_entry_id],
    )
