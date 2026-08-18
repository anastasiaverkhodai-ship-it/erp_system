from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class JournalEntryLine(Base):
    __tablename__ = "journal_entry_lines"

    __table_args__ = (
        UniqueConstraint(
            "journal_entry_id",
            "line_no",
            name="uq_journal_entry_line_number",
        ),
        CheckConstraint(
            "debit >= 0",
            name="ck_journal_entry_line_debit_nonnegative",
        ),
        CheckConstraint(
            "credit >= 0",
            name="ck_journal_entry_line_credit_nonnegative",
        ),
        CheckConstraint(
            """
            (debit > 0 AND credit = 0)
            OR
            (credit > 0 AND debit = 0)
            """,
            name="ck_journal_entry_line_one_side",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    journal_entry_id: Mapped[int] = mapped_column(
        ForeignKey(
            "journal_entries.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    line_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "accounts.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    debit: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        nullable=False,
    )

    credit: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    journal_entry = relationship(
        "JournalEntry",
        back_populates="lines",
    )