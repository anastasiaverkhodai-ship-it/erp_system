"""add employee salary rates

Revision ID: 7f3c91d42e10
Revises: 13c2a7e9b001
"""

from alembic import op
import sqlalchemy as sa


revision = "7f3c91d42e10"
down_revision = "13c2a7e9b001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "employee_salary_rates",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "employment_contract_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "rate_type",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(length=3),
            nullable=False,
        ),
        sa.Column(
            "effective_from",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "effective_to",
            sa.Date(),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            nullable=True,
        ),
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
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_employee_salary_rates",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_employee_salary_rates_company_id_id",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "employment_contract_id",
            ],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name=(
                "fk_employee_salary_rates_"
                "company_contract"
            ),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "rate_type IN ('monthly','hourly')",
            name="ck_employee_salary_rates_rate_type",
        ),
        sa.CheckConstraint(
            "amount > 0",
            name=(
                "ck_employee_salary_rates_"
                "amount_positive"
            ),
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3 "
            "AND currency_code = upper(currency_code)",
            name=(
                "ck_employee_salary_rates_currency"
            ),
        ),
        sa.CheckConstraint(
            "effective_to IS NULL "
            "OR effective_to >= effective_from",
            name=(
                "ck_employee_salary_rates_date_range"
            ),
        ),
    )

    op.create_index(
        "ix_employee_salary_rates_company_contract",
        "employee_salary_rates",
        [
            "company_id",
            "employment_contract_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_employee_salary_rates_company_effective_from",
        "employee_salary_rates",
        [
            "company_id",
            "effective_from",
        ],
        unique=False,
    )

    op.drop_constraint(
        "ck_hr_changes_entity_type",
        "hr_changes",
        type_="check",
    )

    op.create_check_constraint(
        "ck_hr_changes_entity_type",
        "hr_changes",
        "entity_type IN "
        "('employee','department','position',"
        "'employment_contract','salary_rate')",
    )


def downgrade():
    op.drop_constraint(
        "ck_hr_changes_entity_type",
        "hr_changes",
        type_="check",
    )

    op.create_check_constraint(
        "ck_hr_changes_entity_type",
        "hr_changes",
        "entity_type IN "
        "('employee','department','position',"
        "'employment_contract')",
    )

    op.drop_index(
        "ix_employee_salary_rates_company_effective_from",
        table_name="employee_salary_rates",
    )

    op.drop_index(
        "ix_employee_salary_rates_company_contract",
        table_name="employee_salary_rates",
    )

    op.drop_table("employee_salary_rates")
