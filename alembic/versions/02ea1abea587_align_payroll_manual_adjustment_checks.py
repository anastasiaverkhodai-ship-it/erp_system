"""align payroll manual adjustment checks

Revision ID: 02ea1abea587
Revises: ce93fa6854e9
"""

from typing import Sequence, Union

from alembic import op


revision: str = "02ea1abea587"
down_revision: Union[str, None] = "ce93fa6854e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_payroll_calculation_lines_rate",
        "payroll_calculation_lines",
        type_="check",
    )
    op.drop_constraint(
        "ck_payroll_calculation_lines_amount",
        "payroll_calculation_lines",
        type_="check",
    )

    op.create_check_constraint(
        "ck_payroll_calculation_lines_rate",
        "payroll_calculation_lines",
        "(line_type IN ('salary','supplement','vacation_pay','sick_pay') AND rate IS NOT NULL AND rate >= 0) OR (line_type = 'manual_adjustment' AND (rate IS NULL OR rate >= 0))",
    )
    op.create_check_constraint(
        "ck_payroll_calculation_lines_amount",
        "payroll_calculation_lines",
        "(line_type IN ('salary','supplement','vacation_pay','sick_pay') AND amount >= 0) OR line_type = 'manual_adjustment'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_payroll_calculation_lines_amount",
        "payroll_calculation_lines",
        type_="check",
    )
    op.drop_constraint(
        "ck_payroll_calculation_lines_rate",
        "payroll_calculation_lines",
        type_="check",
    )

    op.create_check_constraint(
        "ck_payroll_calculation_lines_rate",
        "payroll_calculation_lines",
        "(line_type IN ('salary','supplement','vacation_pay','sick_pay') AND rate >= 0) OR line_type = 'manual_adjustment'",
    )
    op.create_check_constraint(
        "ck_payroll_calculation_lines_amount",
        "payroll_calculation_lines",
        "(line_type IN ('salary','supplement','vacation_pay','sick_pay') AND amount >= 0) OR line_type = 'manual_adjustment'",
    )
