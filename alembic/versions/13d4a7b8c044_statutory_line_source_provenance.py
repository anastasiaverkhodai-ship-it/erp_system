"""Dated statutory result-to-payroll income provenance.

Revision ID: 13d4a7b8c044
Revises: 13d4a7b8c043

Creates source-link storage without rewriting existing payroll data.
"""

from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c044"
down_revision = "13d4a7b8c043"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_psrl_company_id",
        "payroll_statutory_result_lines",
        ["company_id", "id"],
    )

    op.create_table(
        "payroll_statutory_line_sources",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "statutory_line_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "payroll_calculation_line_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "source_amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "income_effective_from",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "income_effective_to",
            sa.Date(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "statutory_line_id"],
            [
                "payroll_statutory_result_lines.company_id",
                "payroll_statutory_result_lines.id",
            ],
            name="fk_psls_company_statutory_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "payroll_calculation_line_id"],
            [
                "payroll_calculation_lines.company_id",
                "payroll_calculation_lines.id",
            ],
            name="fk_psls_company_income_line",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "statutory_line_id",
            "payroll_calculation_line_id",
            name="uq_psls_tax_income_link",
        ),
        sa.CheckConstraint(
            "source_amount >= 0 "
            "AND source_amount <> 'NaN'::numeric",
            name="ck_psls_source_amount",
        ),
        sa.CheckConstraint(
            "income_effective_to >= income_effective_from",
            name="ck_psls_income_dates",
        ),
    )

    op.create_index(
        "ix_psls_company_income_line",
        "payroll_statutory_line_sources",
        ["company_id", "payroll_calculation_line_id"],
    )


def downgrade():
    op.drop_index(
        "ix_psls_company_income_line",
        table_name="payroll_statutory_line_sources",
    )
    op.drop_table("payroll_statutory_line_sources")
    op.drop_constraint(
        "uq_psrl_company_id",
        "payroll_statutory_result_lines",
        type_="unique",
    )
