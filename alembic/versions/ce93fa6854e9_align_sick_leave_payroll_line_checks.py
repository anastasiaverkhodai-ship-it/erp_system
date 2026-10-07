"""align sick leave payroll line checks

Revision ID: ce93fa6854e9
Revises: 521ef6b24c56
Create Date: 2026-10-07 11:17:09.145743

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ce93fa6854e9'
down_revision: Union[str, Sequence[str], None] = '521ef6b24c56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_amount_by_type
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_line_no_positive
        """
    )

    op.execute(
        """
        ALTER TABLE payroll_calculation_lines
        DROP CONSTRAINT IF EXISTS ck_payroll_calculation_lines_quantity_nonnegative
        """
    )

    op.create_check_constraint(
        "ck_payroll_calculation_line_sick_source",
        "payroll_calculation_lines",
        "(line_type = 'sick_pay') = "
        "(source_sick_leave_calculation_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_payroll_calculation_line_sick_source",
        "payroll_calculation_lines",
        type_="check",
    )

    op.create_check_constraint(
        "ck_payroll_calculation_lines_quantity_nonnegative",
        "payroll_calculation_lines",
        "quantity >= 0",
    )

    op.create_check_constraint(
        "ck_payroll_calculation_lines_line_no_positive",
        "payroll_calculation_lines",
        "line_no > 0",
    )

    op.create_check_constraint(
        "ck_payroll_calculation_lines_amount_by_type",
        "payroll_calculation_lines",
        "(line_type IN ('salary','supplement','vacation_pay') "
        "AND amount >= 0) OR line_type = 'manual_adjustment'",
    )
