"""add accounting rule to journal entries

Revision ID: a270e5ffcd87
Revises: 9ac7557673fb
Create Date: 2026-08-18 13:29:59.360929

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a270e5ffcd87"
down_revision: Union[str, Sequence[str], None] = "9ac7557673fb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "journal_entries",
        sa.Column(
            "accounting_rule_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_journal_entries_accounting_rule_id",
        "journal_entries",
        ["accounting_rule_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_journal_entries_accounting_rule_id",
        "journal_entries",
        "accounting_rules",
        ["accounting_rule_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_constraint(
        "fk_journal_entries_accounting_rule_id",
        "journal_entries",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_journal_entries_accounting_rule_id",
        table_name="journal_entries",
    )

    op.drop_column(
        "journal_entries",
        "accounting_rule_id",
    )