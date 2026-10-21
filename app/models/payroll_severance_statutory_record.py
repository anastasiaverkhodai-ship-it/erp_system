"""Append-only severance statutory classification snapshot storage.

The stored policy evidence is not automatically legally approved.
Financial entrypoints remain fail-closed pending acceptance.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    text,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.payroll import Base


class PayrollSeveranceStatutoryRecord(Base):
    __tablename__ = "payroll_severance_statutory_snapshots"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    payroll_calculation_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    income_date: Mapped[date] = mapped_column(
        Date, nullable=False
    )
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False
    )
    ordinary_gross: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    severance_gross: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    total_gross: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    severance_source_ids: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False
    )
    policy_code: Mapped[str] = mapped_column(
        String(150), nullable=False
    )
    policy_valid_from: Mapped[date] = mapped_column(
        Date, nullable=False
    )
    policy_valid_to: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    source_reference: Mapped[str] = mapped_column(
        String(1000), nullable=False
    )
    income_code_4df: Mapped[str] = mapped_column(
        String(3), nullable=False
    )
    personal_income_tax_base: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    military_levy_base: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    unified_social_contribution_base: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    fingerprint_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "payroll_calculation_id"],
            [
                "payroll_calculations.company_id",
                "payroll_calculations.id",
            ],
            name="fk_psss_calculation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id", "payroll_calculation_id",
            name="uq_psss_calculation",
        ),
        UniqueConstraint(
            "company_id", "id",
            name="uq_psss_company_id",
        ),
        CheckConstraint(
            "version = 1", name="ck_psss_version"
        ),
        CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_psss_currency",
        ),
        CheckConstraint(
            "ordinary_gross >= 0 AND severance_gross > 0 "
            "AND total_gross = ordinary_gross + severance_gross",
            name="ck_psss_gross",
        ),
        CheckConstraint(
            "personal_income_tax_base BETWEEN 0 AND total_gross "
            "AND military_levy_base BETWEEN 0 AND total_gross "
            "AND unified_social_contribution_base "
            "BETWEEN 0 AND total_gross",
            name="ck_psss_bases",
        ),
        CheckConstraint(
            "income_code_4df = '127' "
            "AND length(trim(policy_code)) > 0 "
            "AND length(trim(source_reference)) > 0",
            name="ck_psss_policy",
        ),
        CheckConstraint(
            "policy_valid_from <= income_date "
            "AND (policy_valid_to IS NULL "
            "OR income_date <= policy_valid_to)",
            name="ck_psss_dates",
        ),
        CheckConstraint(
            "fingerprint_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_psss_fingerprint",
        ),
        CheckConstraint(
            "jsonb_typeof(severance_source_ids) = 'array' "
            "AND jsonb_array_length(severance_source_ids) > 0",
            name="ck_psss_sources",
        ),
    )
