"""add company chart of accounts template

Revision ID: b8d4e2a1c6f0
Revises: 701c01bcb132
Create Date: 2026-08-23

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8d4e2a1c6f0"

down_revision: Union[
    str,
    Sequence[str],
    None,
] = "701c01bcb132"

branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None

depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


TEMPLATE_CONSTRAINT = (
    "chart_of_accounts_template_enum"
)


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "chart_of_accounts_template",
            sa.String(length=32),
            server_default="general_291",
            nullable=False,
        ),
    )

    op.create_check_constraint(
        TEMPLATE_CONSTRAINT,
        "companies",
        (
            "chart_of_accounts_template IN "
            "('general_291', 'simplified_186')"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        TEMPLATE_CONSTRAINT,
        "companies",
        type_="check",
    )

    op.drop_column(
        "companies",
        "chart_of_accounts_template",
    )
