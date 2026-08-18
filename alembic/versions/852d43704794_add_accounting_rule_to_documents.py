"""add accounting rule to documents

Revision ID: 852d43704794
Revises: a270e5ffcd87
Create Date: 2026-08-18 14:14:53.931622

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "852d43704794"
down_revision: Union[str, Sequence[str], None] = "a270e5ffcd87"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "documents",
        sa.Column(
            "accounting_rule_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_documents_accounting_rule_id",
        "documents",
        ["accounting_rule_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_documents_accounting_rule_id",
        "documents",
        "accounting_rules",
        ["accounting_rule_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_constraint(
        "fk_documents_accounting_rule_id",
        "documents",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_documents_accounting_rule_id",
        table_name="documents",
    )

    op.drop_column(
        "documents",
        "accounting_rule_id",
    )