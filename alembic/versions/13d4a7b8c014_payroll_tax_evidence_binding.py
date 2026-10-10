"""Bind payroll tax policies to verified evidence.

Revision ID: 13d4a7b8c014
Revises: 13d4a7b8c013
"""

from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c014"
down_revision = "13d4a7b8c013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "payroll_employee_tax_profiles",
        sa.Column(
            "tax_evidence_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "payroll_statutory_rates",
        sa.Column(
            "tax_evidence_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_payroll_tax_profile_evidence",
        "payroll_employee_tax_profiles",
        "payroll_employee_tax_evidence",
        ["company_id", "tax_evidence_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_payroll_statutory_rate_evidence",
        "payroll_statutory_rates",
        "payroll_employee_tax_evidence",
        ["company_id", "tax_evidence_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade():
    op.drop_constraint(
        "fk_payroll_statutory_rate_evidence",
        "payroll_statutory_rates",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_payroll_tax_profile_evidence",
        "payroll_employee_tax_profiles",
        type_="foreignkey",
    )

    op.drop_column(
        "payroll_statutory_rates",
        "tax_evidence_id",
    )

    op.drop_column(
        "payroll_employee_tax_profiles",
        "tax_evidence_id",
    )
