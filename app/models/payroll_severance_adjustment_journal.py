"""Immutable link between severance proposal and draft GL journal."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollSeveranceAdjustmentJournal(Base):
    __tablename__ = "payroll_severance_adjustment_journals"

    id: Mapped[int] = mapped_column(
        Integer, Identity(), primary_key=True,
    )
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    financial_proposal_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    journal_entry_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    created_by: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "financial_proposal_id"],
            [
                "payroll_severance_financial_proposals.company_id",
                "payroll_severance_financial_proposals.id",
            ],
            name="fk_sev_adjustment_proposal",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "journal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_sev_adjustment_journal",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_sev_adjustment_creator",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id",
            "financial_proposal_id",
            name="uq_sev_adjustment_proposal",
        ),
        UniqueConstraint(
            "company_id",
            "journal_entry_id",
            name="uq_sev_adjustment_journal",
        ),
    )
