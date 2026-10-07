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


class PayrollTaxProfileCategory(str, enum.Enum):
    STANDARD = "standard"
    BENEFIT_ELIGIBLE = "benefit_eligible"
    EXEMPT = "exempt"


class PayrollStatutoryBaseMode(str, enum.Enum):
    GROSS = "gross"
    GROSS_AFTER_BENEFIT = "gross_after_benefit"


class PayrollEmployeeTaxProfile(Base):
    __tablename__ = "payroll_employee_tax_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employment_contract_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PayrollTaxProfileCategory.STANDARD.value,
        server_default=PayrollTaxProfileCategory.STANDARD.value,
    )

    benefit_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    exemption_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
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
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name="fk_payroll_tax_profiles_company_employee",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name="fk_payroll_tax_profiles_company_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_payroll_tax_profiles_created_by",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_tax_profiles_company_id_id",
        ),
        CheckConstraint(
            "category IN ('standard','benefit_eligible','exempt')",
            name="ck_payroll_tax_profiles_category",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_payroll_tax_profiles_effective_range",
        ),
        CheckConstraint(
            "category <> 'benefit_eligible' "
            "OR benefit_code IS NOT NULL",
            name="ck_payroll_tax_profiles_benefit_code",
        ),
        CheckConstraint(
            "category <> 'exempt' "
            "OR exemption_code IS NOT NULL",
            name="ck_payroll_tax_profiles_exemption_code",
        ),
        Index(
            "ix_payroll_tax_profiles_company_employee_effective",
            "company_id",
            "employee_id",
            "effective_from",
        ),
        Index(
            "ix_payroll_tax_profiles_company_contract_effective",
            "company_id",
            "employment_contract_id",
            "effective_from",
        ),
    )


class PayrollStatutoryBaseRule(Base):
    __tablename__ = "payroll_statutory_base_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)

    component: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    employment_kind: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    tax_profile_category: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    base_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PayrollStatutoryBaseMode.GROSS.value,
        server_default=PayrollStatutoryBaseMode.GROSS.value,
    )

    benefit_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    minimum_base_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    maximum_base_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    exemption_applies: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    rule_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    rule_version: Mapped[str] = mapped_column(
        String(100),
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
            name="fk_payroll_statutory_base_rules_company",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_payroll_statutory_base_rules_created_by",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_statutory_base_rules_company_id_id",
        ),
        CheckConstraint(
            "component IN "
            "('personal_income_tax','military_levy',"
            "'unified_social_contribution')",
            name="ck_payroll_statutory_base_rules_component",
        ),
        CheckConstraint(
            "employment_kind IS NULL OR employment_kind IN "
            "('primary','internal_secondary','external_secondary')",
            name="ck_payroll_statutory_base_rules_employment_kind",
        ),
        CheckConstraint(
            "tax_profile_category IS NULL "
            "OR tax_profile_category IN "
            "('standard','benefit_eligible','exempt')",
            name="ck_payroll_statutory_base_rules_profile_category",
        ),
        CheckConstraint(
            "base_mode IN ('gross','gross_after_benefit')",
            name="ck_payroll_statutory_base_rules_base_mode",
        ),
        CheckConstraint(
            "benefit_amount >= 0",
            name="ck_payroll_statutory_base_rules_benefit_nonnegative",
        ),
        CheckConstraint(
            "minimum_base_amount IS NULL "
            "OR minimum_base_amount >= 0",
            name="ck_payroll_statutory_base_rules_min_nonnegative",
        ),
        CheckConstraint(
            "maximum_base_amount IS NULL "
            "OR maximum_base_amount >= 0",
            name="ck_payroll_statutory_base_rules_max_nonnegative",
        ),
        CheckConstraint(
            "minimum_base_amount IS NULL "
            "OR maximum_base_amount IS NULL "
            "OR maximum_base_amount >= minimum_base_amount",
            name="ck_payroll_statutory_base_rules_limit_range",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_payroll_statutory_base_rules_effective_range",
        ),
        CheckConstraint(
            "length(trim(rule_code)) > 0",
            name="ck_payroll_statutory_base_rules_code_nonempty",
        ),
        CheckConstraint(
            "length(trim(rule_version)) > 0",
            name="ck_payroll_statutory_base_rules_version_nonempty",
        ),
        Index(
            "ix_payroll_statutory_base_rules_company_component_effective",
            "company_id",
            "component",
            "effective_from",
        ),
    )
