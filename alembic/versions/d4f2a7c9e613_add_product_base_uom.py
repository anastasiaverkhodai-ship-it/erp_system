"""add product base uom

Revision ID: d4f2a7c9e613
Revises: c9e1f4a6b802

Persist the canonical base unit for a Product.

Legacy rows remain nullable because their historical unit cannot
be inferred safely. New products must provide a registered UOM
through the Product API.

No guessed PCS backfill is performed.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d4f2a7c9e613"
down_revision: str | None = "c9e1f4a6b802"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "base_uom_code",
            sa.String(length=20),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        "ck_products_base_uom_code_nonblank",
        "products",
        (
            "base_uom_code IS NULL OR "
            "length(trim(base_uom_code)) > 0"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_products_base_uom_code_nonblank",
        "products",
        type_="check",
    )

    op.drop_column(
        "products",
        "base_uom_code",
    )
