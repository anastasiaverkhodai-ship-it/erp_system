"""add sick leave payroll line provenance

Revision ID: 521ef6b24c56
Revises: 6a86a7979e8e
Create Date: 2026-10-07 09:59:00.460476

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '521ef6b24c56'
down_revision: Union[str, Sequence[str], None] = '6a86a7979e8e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payroll_calculation_lines",
        sa.Column(
            "source_sick_leave_calculation_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_payroll_calculation_lines_sick_leave",
        "payroll_calculation_lines",
        "payroll_sick_leave_calculations",
        ["company_id", "source_sick_leave_calculation_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_type
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_line_type
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        ADD CONSTRAINT ck_payroll_calculation_lines_type
        CHECK (
            line_type IN (
                'salary',
                'manual_adjustment',
                'supplement',
                'vacation_pay',
                'sick_pay'
            )
        )
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_rate
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_rate_nonnegative
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        ADD CONSTRAINT ck_payroll_calculation_lines_rate
        CHECK (
            (
                line_type IN (
                    'salary',
                    'supplement',
                    'vacation_pay',
                    'sick_pay'
                )
                AND rate IS NOT NULL
                AND rate >= 0
            )
            OR
            (
                line_type = 'manual_adjustment'
                AND (rate IS NULL OR rate >= 0)
            )
        )
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_amount
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_amount_nonnegative
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        ADD CONSTRAINT ck_payroll_calculation_lines_amount
        CHECK (
            (
                line_type IN (
                    'salary',
                    'supplement',
                    'vacation_pay',
                    'sick_pay'
                )
                AND amount >= 0
            )
            OR line_type = 'manual_adjustment'
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_amount
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        ADD CONSTRAINT ck_payroll_calculation_lines_amount
        CHECK (
            (
                line_type IN (
                    'salary',
                    'supplement',
                    'vacation_pay'
                )
                AND amount >= 0
            )
            OR line_type = 'manual_adjustment'
        )
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_rate
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        ADD CONSTRAINT ck_payroll_calculation_lines_rate
        CHECK (
            (
                line_type IN (
                    'salary',
                    'supplement',
                    'vacation_pay'
                )
                AND rate IS NOT NULL
                AND rate >= 0
            )
            OR
            (
                line_type = 'manual_adjustment'
                AND (rate IS NULL OR rate >= 0)
            )
        )
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_type
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        ADD CONSTRAINT ck_payroll_calculation_lines_type
        CHECK (
            line_type IN (
                'salary',
                'manual_adjustment',
                'supplement',
                'vacation_pay'
            )
        )
        """
    )

    op.drop_constraint(
        "fk_payroll_calculation_lines_sick_leave",
        "payroll_calculation_lines",
        type_="foreignkey",
    )

    op.drop_column(
        "payroll_calculation_lines",
        "source_sick_leave_calculation_id",
    )
