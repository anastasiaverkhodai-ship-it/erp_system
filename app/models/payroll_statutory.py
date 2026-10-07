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

    __table_args__ = (
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
