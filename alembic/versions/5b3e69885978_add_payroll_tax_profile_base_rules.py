"""add payroll tax profile and statutory base rules

Revision ID: 5b3e69885978
Revises: 5a4dbbc494b5
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5b3e69885978"
down_revision: Union[str, Sequence[str], None] = "5a4dbbc494b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payroll_employee_tax_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column(
            "employment_contract_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.String(length=32),
            server_default="standard",
            nullable=False,
        ),
        sa.Column(
            "benefit_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "exemption_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN "
            "('standard','benefit_eligible','exempt')",
            name="ck_payroll_tax_profiles_category",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL "
            "OR effective_to >= effective_from",
            name="ck_payroll_tax_profiles_effective_range",
        ),
        sa.CheckConstraint(
            "category <> 'benefit_eligible' "
            "OR benefit_code IS NOT NULL",
            name="ck_payroll_tax_profiles_benefit_code",
        ),
        sa.CheckConstraint(
            "category <> 'exempt' "
            "OR exemption_code IS NOT NULL",
            name="ck_payroll_tax_profiles_exemption_code",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name="fk_payroll_tax_profiles_company_employee",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name="fk_payroll_tax_profiles_company_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_payroll_tax_profiles_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_tax_profiles_company_id_id",
        ),
    )

    op.create_index(
        "ix_payroll_tax_profiles_company_employee_effective",
        "payroll_employee_tax_profiles",
        ["company_id", "employee_id", "effective_from"],
        unique=False,
    )

    op.create_index(
        "ix_payroll_tax_profiles_company_contract_effective",
        "payroll_employee_tax_profiles",
        ["company_id", "employment_contract_id", "effective_from"],
        unique=False,
    )

    op.create_table(
        "payroll_statutory_base_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "component",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "employment_kind",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column(
            "tax_profile_category",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column(
            "base_mode",
            sa.String(length=32),
            server_default="gross",
            nullable=False,
        ),
        sa.Column(
            "benefit_amount",
            sa.Numeric(18, 2),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "minimum_base_amount",
            sa.Numeric(18, 2),
            nullable=True,
        ),
        sa.Column(
            "maximum_base_amount",
            sa.Numeric(18, 2),
            nullable=True,
        ),
        sa.Column(
            "exemption_applies",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "rule_code",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "rule_version",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "component IN "
            "('personal_income_tax','military_levy',"
            "'unified_social_contribution')",
            name="ck_payroll_statutory_base_rules_component",
        ),
        sa.CheckConstraint(
            "employment_kind IS NULL "
            "OR employment_kind IN "
            "('primary','internal_secondary','external_secondary')",
            name="ck_payroll_statutory_base_rules_employment_kind",
        ),
        sa.CheckConstraint(
            "tax_profile_category IS NULL "
            "OR tax_profile_category IN "
            "('standard','benefit_eligible','exempt')",
            name="ck_payroll_statutory_base_rules_profile_category",
        ),
        sa.CheckConstraint(
            "base_mode IN ('gross','gross_after_benefit')",
            name="ck_payroll_statutory_base_rules_base_mode",
        ),
        sa.CheckConstraint(
            "benefit_amount >= 0",
            name="ck_payroll_statutory_base_rules_benefit_nonnegative",
        ),
        sa.CheckConstraint(
            "minimum_base_amount IS NULL "
            "OR minimum_base_amount >= 0",
            name="ck_payroll_statutory_base_rules_min_nonnegative",
        ),
        sa.CheckConstraint(
            "maximum_base_amount IS NULL "
            "OR maximum_base_amount >= 0",
            name="ck_payroll_statutory_base_rules_max_nonnegative",
        ),
        sa.CheckConstraint(
            "minimum_base_amount IS NULL "
            "OR maximum_base_amount IS NULL "
            "OR maximum_base_amount >= minimum_base_amount",
            name="ck_payroll_statutory_base_rules_limit_range",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL "
            "OR effective_to >= effective_from",
            name="ck_payroll_statutory_base_rules_effective_range",
        ),
        sa.CheckConstraint(
            "length(trim(rule_code)) > 0",
            name="ck_payroll_statutory_base_rules_code_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(rule_version)) > 0",
            name="ck_payroll_statutory_base_rules_version_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_payroll_statutory_base_rules_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_payroll_statutory_base_rules_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_statutory_base_rules_company_id_id",
        ),
    )

    op.create_index(
        "ix_payroll_statutory_base_rules_company_component_effective",
        "payroll_statutory_base_rules",
        ["company_id", "component", "effective_from"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payroll_statutory_base_rules_company_component_effective",
        table_name="payroll_statutory_base_rules",
    )
    op.drop_table("payroll_statutory_base_rules")

    op.drop_index(
        "ix_payroll_tax_profiles_company_contract_effective",
        table_name="payroll_employee_tax_profiles",
    )
    op.drop_index(
        "ix_payroll_tax_profiles_company_employee_effective",
        table_name="payroll_employee_tax_profiles",
    )
    op.drop_table("payroll_employee_tax_profiles")
