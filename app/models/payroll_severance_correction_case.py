"""Immutable review-stage severance correction case."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Identity,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollSeveranceCorrectionCase(Base):
    __tablename__ = "payroll_severance_correction_cases"

    id: Mapped[int] = mapped_column(
        Integer, Identity(), primary_key=True
    )
    company_id: Mapped[int] = mapped_column(Integer)
    payroll_input_id: Mapped[int] = mapped_column(Integer)
    original_payroll_calculation_id: Mapped[int] = mapped_column(Integer)
    original_severance_source_id: Mapped[int] = mapped_column(Integer)

    request_key: Mapped[str] = mapped_column(String(120))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    evidence_reference: Mapped[str] = mapped_column(String(500))
    evidence_date: Mapped[date] = mapped_column(Date)

    original_severance_gross: Mapped[Decimal] = mapped_column(
        Numeric(18, 2)
    )
    posted_payroll_amount_at_opening: Mapped[Decimal] = mapped_column(
        Numeric(18, 2)
    )
    as_of_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(32),
        server_default=text("'review_required'"),
    )
    created_by: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id", "id",
            name="uq_sev_case_company_id",
        ),
        ForeignKeyConstraint(
            ["company_id", "original_payroll_calculation_id"],
            [
                "payroll_calculations.company_id",
                "payroll_calculations.id",
            ],
            name="fk_sev_case_payroll_calculation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id", "request_key",
            name="uq_sev_case_company_request",
        ),
        CheckConstraint(
            "original_severance_gross > 0 "
            "AND posted_payroll_amount_at_opening > 0",
            name="ck_sev_case_positive_financial_sources",
        ),
        CheckConstraint(
            "status = 'review_required'",
            name="ck_sev_case_review_only",
        ),
        CheckConstraint(
            "length(trim(evidence_reference)) >= 10",
            name="ck_sev_case_document_reference",
        ),
        Index(
            "uq_sev_case_active_calculation",
            "company_id",
            "original_payroll_calculation_id",
            unique=True,
            postgresql_where=text(
                "status = 'review_required'"
            ),
        ),
    )
