"""allow signed manual adjustment rate

Revision ID: 840d5bc18de3
Revises: 02ea1abea587
"""

from typing import Sequence, Union

from alembic import op


revision: str = "840d5bc18de3"
down_revision: Union[str, None] = "02ea1abea587"
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
        "(line_type IN ('salary','supplement','vacation_pay','sick_pay') AND rate IS NOT NULL AND rate >= 0) OR (line_type = 'manual_adjustment' AND rate IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_payroll_calculation_lines_amount",
        "payroll_calculation_lines",
        "(line_type IN ('salary','supplement','vacation_pay','sick_pay') AND amount >= 0) OR line_type = 'manual_adjustment'",
    )


def downgrade() -> None:
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
