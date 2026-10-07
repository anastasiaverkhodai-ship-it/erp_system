"""align payroll calculation line base checks

Revision ID: 5a4dbbc494b5
Revises: 840d5bc18de3
"""

from typing import Sequence, Union

from alembic import op


revision: str = "5a4dbbc494b5"
down_revision: Union[str, None] = "840d5bc18de3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_payroll_calculation_lines_line_no_positive",
        "payroll_calculation_lines",
        "line_no > 0",
    )
    op.create_check_constraint(
        "ck_payroll_calculation_lines_quantity_nonnegative",
        "payroll_calculation_lines",
        "quantity >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_payroll_calculation_lines_quantity_nonnegative",
        "payroll_calculation_lines",
        type_="check",
    )
    op.drop_constraint(
        "ck_payroll_calculation_lines_line_no_positive",
        "payroll_calculation_lines",
        type_="check",
    )
