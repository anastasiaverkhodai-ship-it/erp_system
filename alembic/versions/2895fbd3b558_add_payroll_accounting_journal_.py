"""add payroll accounting journal provenance

Revision ID: 2895fbd3b558
Revises: 6c9f7e2a41bd
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2895fbd3b558"
down_revision: Union[str, None] = "6c9f7e2a41bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "journal_entries",
        sa.Column(
            "payroll_calculation_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_je_company_payroll_calculation",
        "journal_entries",
        "payroll_calculations",
        ["company_id", "payroll_calculation_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_je_payroll_calculation",
        "journal_entries",
        ["payroll_calculation_id"],
        unique=False,
    )
    op.create_index(
        "uq_je_original_payroll_calculation",
        "journal_entries",
        ["company_id", "payroll_calculation_id"],
        unique=True,
        postgresql_where=sa.text(
            "payroll_calculation_id IS NOT NULL "
            "AND reversal_of_id IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_je_original_payroll_calculation",
        table_name="journal_entries",
    )
    op.drop_index(
        "ix_je_payroll_calculation",
        table_name="journal_entries",
    )
    op.drop_constraint(
        "fk_je_company_payroll_calculation",
        "journal_entries",
        type_="foreignkey",
    )
    op.drop_column(
        "journal_entries",
        "payroll_calculation_id",
    )
