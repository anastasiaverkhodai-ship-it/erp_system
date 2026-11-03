"""Immutable review-stage severance financial proposal."""

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollSeveranceFinancialProposal(Base):
    __tablename__ = "payroll_severance_financial_proposals"

    id: Mapped[int] = mapped_column(
        Integer, Identity(), primary_key=True
    )
    company_id: Mapped[int] = mapped_column(Integer)
    correction_case_id: Mapped[int] = mapped_column(Integer)

    request_key: Mapped[str] = mapped_column(String(120))
    request_fingerprint: Mapped[str] = mapped_column(String(64))

    document_reference: Mapped[str] = mapped_column(String(500))
    document_date: Mapped[date] = mapped_column(Date)

    original_financial: Mapped[dict] = mapped_column(JSONB)
    proposed_financial: Mapped[dict] = mapped_column(JSONB)

    status: Mapped[str] = mapped_column(
        String(32),
        server_default=text("'review_required'"),
    )

    submitted_by: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "correction_case_id"],
            [
                "payroll_severance_correction_cases.company_id",
                "payroll_severance_correction_cases.id",
            ],
            name="fk_sev_financial_proposal_case",
            ondelete="RESTRICT",
        ),

        UniqueConstraint(
            "company_id", "request_key",
            name="uq_sev_financial_proposal_request",
        ),

        UniqueConstraint(
            "company_id", "correction_case_id",
            name="uq_sev_financial_proposal_case",
        ),

        CheckConstraint(
            "status = 'review_required'",
            name="ck_sev_financial_proposal_review_only",
        ),

        CheckConstraint(
            "length(trim(document_reference)) >= 10",
            name="ck_sev_financial_proposal_document",
        ),
    )
