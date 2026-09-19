"""harden accounting period state consistency

Revision ID: 230ce697c07a
Revises: d4a1b2c3e586
Create Date: 2026-09-19 19:24:16.185450

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '230ce697c07a'
down_revision: Union[str, Sequence[str], None] = 'd4a1b2c3e586'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        "ck_accounting_period_state",
        "accounting_periods",
        "(status = 'open' AND is_locked = false) OR "
        "(status = 'closed' AND is_locked = true)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_accounting_period_state",
        "accounting_periods",
        type_="check",
    )
