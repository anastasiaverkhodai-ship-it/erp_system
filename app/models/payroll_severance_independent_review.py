"""Immutable severance independent-review record."""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollSeveranceIndependentReview(Base):
    __tablename__ = "payroll_severance_independent_reviews"

    id: Mapped[int] = mapped_column(
        Integer, Identity(), primary_key=True,
    )
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    financial_proposal_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    reviewer_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    request_key: Mapped[str] = mapped_column(
        String(120), nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    review_reference: Mapped[str] = mapped_column(
        String(500), nullable=False,
    )
    review_date: Mapped[date] = mapped_column(
        Date, nullable=False,
    )
    decision: Mapped[str] = mapped_column(
        String(40), nullable=False,
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
            name="fk_sev_review_proposal",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"],
            name="fk_sev_review_user",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id", "financial_proposal_id",
            name="uq_sev_review_proposal",
        ),
        UniqueConstraint(
            "company_id", "request_key",
            name="uq_sev_review_request",
        ),
        CheckConstraint(
            "decision IN "
            "('reviewed_pending_statutory', 'rejected')",
            name="ck_sev_review_decision",
        ),
        CheckConstraint(
            "length(trim(review_reference)) >= 10",
            name="ck_sev_review_reference",
        ),
    )
