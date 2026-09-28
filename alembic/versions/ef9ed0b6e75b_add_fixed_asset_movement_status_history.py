"""add fixed asset movement status history

Revision ID: ef9ed0b6e75b
Revises: 31458c806d54
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ef9ed0b6e75b"
down_revision: Union[str, Sequence[str], None] = "31458c806d54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fixed_asset_card_history",
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "READY_FOR_COMMISSIONING",
                "IN_SERVICE",
                "SUSPENDED",
                "DISPOSED",
                name="fixed_asset_status",
                native_enum=False,
            ),
            nullable=True,
        ),
    )

    op.execute(
        """
        UPDATE fixed_asset_card_history AS h
        SET status = a.status
        FROM fixed_assets AS a
        WHERE a.id = h.fixed_asset_id
          AND a.company_id = h.company_id
          AND h.status IS NULL
        """
    )

    bind = op.get_bind()

    missing = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM fixed_asset_card_history
            WHERE status IS NULL
            """
        )
    ).scalar_one()

    if missing:
        raise RuntimeError(
            "cannot make fixed_asset_card_history.status "
            f"NOT NULL: {missing} historical rows "
            "could not be backfilled"
        )

    op.alter_column(
        "fixed_asset_card_history",
        "status",
        existing_type=sa.Enum(
            "DRAFT",
            "READY_FOR_COMMISSIONING",
            "IN_SERVICE",
            "SUSPENDED",
            "DISPOSED",
            name="fixed_asset_status",
            native_enum=False,
        ),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column(
        "fixed_asset_card_history",
        "status",
    )
