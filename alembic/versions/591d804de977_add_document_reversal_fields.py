"""add document reversal fields

Revision ID: 591d804de977
Revises: 6c54ca4e0755
Create Date: 2026-08-17 19:13:21.722966

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "591d804de977"
down_revision: Union[str, Sequence[str], None] = "6c54ca4e0755"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "documents",
        sa.Column(
            "reversed_at",
            sa.DateTime(),
            nullable=True,
        ),
    )

    op.add_column(
        "documents",
        sa.Column(
            "reversed_by",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_documents_reversed_by_users",
        "documents",
        "users",
        ["reversed_by"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_constraint(
        "fk_documents_reversed_by_users",
        "documents",
        type_="foreignkey",
    )

    op.drop_column(
        "documents",
        "reversed_by",
    )

    op.drop_column(
        "documents",
        "reversed_at",
    )