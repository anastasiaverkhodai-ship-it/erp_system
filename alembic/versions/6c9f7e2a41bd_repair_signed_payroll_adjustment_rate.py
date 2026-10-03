"""repair signed payroll adjustment rate

Revision ID: 6c9f7e2a41bd
Revises: 4b9db9df2485
Create Date: 2026-10-03

"""

from typing import Sequence, Union

from alembic import op


revision: str = "6c9f7e2a41bd"
down_revision: Union[str, Sequence[str], None] = "4b9db9df2485"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_payroll_calculation_lines_rate_nonnegative",
        "payroll_calculation_lines",
        type_="check",
    )

    op.create_check_constraint(
        "ck_payroll_calculation_lines_rate_nonnegative",
        "payroll_calculation_lines",
        "(line_type = 'salary' AND rate >= 0) "
        "OR line_type = 'manual_adjustment'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_payroll_calculation_lines_rate_nonnegative",
        "payroll_calculation_lines",
        type_="check",
    )

    op.create_check_constraint(
        "ck_payroll_calculation_lines_rate_nonnegative",
        "payroll_calculation_lines",
        "rate >= 0",
    )
