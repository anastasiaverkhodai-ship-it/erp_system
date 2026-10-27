from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollStatutoryComponent(str, enum.Enum):
    PERSONAL_INCOME_TAX = "personal_income_tax"
    MILITARY_LEVY = "military_levy"
    UNIFIED_SOCIAL_CONTRIBUTION = "unified_social_contribution"


class PayrollStatutoryRate(Base):
    __tablename__ = "payroll_statutory_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    company_id: Mapped[int] = mapped_column(Integer, nullable=False)

    employee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)

    component: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 8),
        nullable=False,
    )

    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    effective_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    created_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    tax_evidence_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "tax_evidence_id"],
            ["payroll_employee_tax_evidence.company_id", "payroll_employee_tax_evidence.id"],
            name="fk_payroll_statutory_rate_evidence",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(['company_id', 'employee_id'], ['employees.company_id', 'employees.id'],
            name='fk_payroll_statutory_rate_employee', ondelete='RESTRICT'),
        CheckConstraint("employee_id IS NULL OR (source_reference IS NOT NULL AND length(trim(source_reference)) > 0)",
            name='ck_payroll_statutory_rate_evidence'),
        ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_payroll_statutory_rates_company",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_payroll_statutory_rates_created_by",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_statutory_rates_company_id_id",
        ),
        CheckConstraint(
            "component IN "
            "('personal_income_tax', 'military_levy', "
            "'unified_social_contribution')",
            name="ck_payroll_statutory_rates_component",
        ),
        CheckConstraint(
            "rate >= 0 AND rate <= 1",
            name="ck_payroll_statutory_rates_rate_range",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_payroll_statutory_rates_effective_range",
        ),
        Index(
            "ix_payroll_statutory_rates_company_component_effective",
            "company_id",
            "component",
            "effective_from",
        ),
    )


class PayrollStatutoryResult(Base):
    __tablename__ = "payroll_statutory_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    company_id: Mapped[int] = mapped_column(Integer, nullable=False)

    payroll_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    gross_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    employee_withholding_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    employer_contribution_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    net_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    calculated_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "payroll_calculation_id"],
            [
                "payroll_calculations.company_id",
                "payroll_calculations.id",
            ],
            name="fk_payroll_statutory_results_company_calculation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["calculated_by"],
            ["users.id"],
            name="fk_payroll_statutory_results_calculated_by",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_statutory_results_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_calculation_id",
            name="uq_payroll_statutory_results_company_calculation",
        ),
        CheckConstraint(
            "gross_amount >= 0",
            name="ck_payroll_statutory_results_gross_nonnegative",
        ),
        CheckConstraint(
            "employee_withholding_amount >= 0",
            name="ck_payroll_statutory_results_employee_withholding_nonnegative",
        ),
        CheckConstraint(
            "employer_contribution_amount >= 0",
            name="ck_payroll_statutory_results_employer_contribution_nonnegative",
        ),
        CheckConstraint(
            "net_amount >= 0",
            name="ck_payroll_statutory_results_net_nonnegative",
        ),
        CheckConstraint(
            "net_amount = gross_amount - employee_withholding_amount",
            name="ck_payroll_statutory_results_net_reconciliation",
        ),
        CheckConstraint(
            "currency_code = upper(currency_code) "
            "AND char_length(currency_code) = 3",
            name="ck_payroll_statutory_results_currency",
        ),
        Index(
            "ix_payroll_statutory_results_company_calculation",
            "company_id",
            "payroll_calculation_id",
        ),
    )


class PayrollStatutoryResultLine(Base):
    employee_gross_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    benefit_income_limit_applied: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    __tablename__ = "payroll_statutory_result_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    company_id: Mapped[int] = mapped_column(Integer, nullable=False)

    payroll_statutory_result_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    line_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    component: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    base_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 8),
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    source_rate_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    source_tax_profile_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    source_base_rule_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    gross_base_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    benefit_amount_applied: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    minimum_base_amount_applied: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    maximum_base_amount_applied: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    exemption_applied: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    base_rule_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    base_rule_version: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    rate_effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    rate_effective_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_psrl_company_id",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_statutory_result_id"],
            [
                "payroll_statutory_results.company_id",
                "payroll_statutory_results.id",
            ],
            name="fk_payroll_statutory_lines_company_result",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "source_rate_id"],
            [
                "payroll_statutory_rates.company_id",
                "payroll_statutory_rates.id",
            ],
            name="fk_payroll_statutory_lines_company_rate",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "source_tax_profile_id"],
            [
                "payroll_employee_tax_profiles.company_id",
                "payroll_employee_tax_profiles.id",
            ],
            name=(
                "fk_payroll_statutory_result_lines_"
                "company_tax_profile"
            ),
        ),
        ForeignKeyConstraint(
            ["company_id", "source_base_rule_id"],
            [
                "payroll_statutory_base_rules.company_id",
                "payroll_statutory_base_rules.id",
            ],
            name=(
                "fk_payroll_statutory_result_lines_"
                "company_base_rule"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "payroll_statutory_result_id",
            "line_no",
            name="uq_payroll_statutory_lines_result_line_no",
        ),
        CheckConstraint(
            "line_no > 0",
            name="ck_payroll_statutory_lines_line_no_positive",
        ),
        CheckConstraint(
            "component IN "
            "('personal_income_tax', 'military_levy', "
            "'unified_social_contribution')",
            name="ck_payroll_statutory_lines_component",
        ),
        CheckConstraint(
            "base_amount >= 0",
            name="ck_payroll_statutory_lines_base_nonnegative",
        ),
        CheckConstraint(
            "rate >= 0 AND rate <= 1",
            name="ck_payroll_statutory_lines_rate_range",
        ),
        CheckConstraint(
            "amount >= 0",
            name="ck_payroll_statutory_lines_amount_nonnegative",
        ),
        CheckConstraint(
            "rate_effective_to IS NULL "
            "OR rate_effective_to >= rate_effective_from",
            name="ck_payroll_statutory_lines_rate_effective_range",
        ),
        CheckConstraint(
            "currency_code = upper(currency_code) "
            "AND char_length(currency_code) = 3",
            name="ck_payroll_statutory_lines_currency",
        ),
        Index(
            "ix_payroll_statutory_lines_company_result",
            "company_id",
            "payroll_statutory_result_id",
        ),
    )


class PayrollStatutoryLineSource(Base):
    """Persist a verified statutory line's dated salary-source links."""

    __tablename__ = "payroll_statutory_line_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    statutory_line_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    payroll_calculation_line_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    source_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    income_effective_from: Mapped[date] = mapped_column(
        Date, nullable=False
    )
    income_effective_to: Mapped[date] = mapped_column(
        Date, nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "statutory_line_id"],
            [
                "payroll_statutory_result_lines.company_id",
                "payroll_statutory_result_lines.id",
            ],
            name="fk_psls_company_statutory_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_calculation_line_id"],
            [
                "payroll_calculation_lines.company_id",
                "payroll_calculation_lines.id",
            ],
            name="fk_psls_company_income_line",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id",
            "statutory_line_id",
            "payroll_calculation_line_id",
            name="uq_psls_tax_income_link",
        ),
        CheckConstraint(
            "source_amount >= 0 "
            "AND source_amount <> 'NaN'::numeric",
            name="ck_psls_source_amount",
        ),
        CheckConstraint(
            "income_effective_to >= income_effective_from",
            name="ck_psls_income_dates",
        ),
        Index(
            "ix_psls_company_income_line",
            "company_id",
            "payroll_calculation_line_id",
        ),
    )


# H1-B2-B1-R1 DATED STATUTORY SEAL ORM
from sqlalchemy import (
    Column as SealColumn,
    Integer as SealInteger,
    DateTime as SealDateTime,
    ForeignKeyConstraint as SealForeignKeyConstraint,
    PrimaryKeyConstraint as SealPrimaryKeyConstraint,
    text as seal_sql_text,
)


class PayrollDatedStatutorySeal(Base):
    """Immutable completion marker for dated statutory evidence."""

    __tablename__ = "payroll_dated_statutory_seals"

    company_id = SealColumn(
        SealInteger,
        nullable=False,
    )

    payroll_statutory_result_id = SealColumn(
        SealInteger,
        nullable=False,
    )

    sealed_at = SealColumn(
        SealDateTime(timezone=True),
        nullable=False,
        server_default=seal_sql_text("now()"),
    )

    __table_args__ = (
        SealPrimaryKeyConstraint(
            "company_id",
            "payroll_statutory_result_id",
            name="pk_payroll_dated_statutory_seals",
        ),
        SealForeignKeyConstraint(
            ["company_id", "payroll_statutory_result_id"],
            [
                "payroll_statutory_results.company_id",
                "payroll_statutory_results.id",
            ],
            name="fk_payroll_dated_statutory_seals_result",
            ondelete="RESTRICT",
        ),
    )
