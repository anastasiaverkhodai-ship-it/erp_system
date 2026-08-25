"""add persistent reservation movements

Revision ID: aff9cb99d26a
Revises: 1ed0e8211fc7
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "aff9cb99d26a"
down_revision: Union[str, Sequence[str], None] = (
    "1ed0e8211fc7"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The composite FK from reservation_movements requires
    # this candidate key to exist first.
    op.create_unique_constraint(
        "uq_trade_document_lines_reservation_source",
        "trade_document_lines",
        [
            "company_id",
            "trade_document_id",
            "id",
            "product_id",
            "warehouse_id",
        ],
    )

    op.create_table(
        "reservation_movements",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "source_document_line_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "quantity",
            sa.Numeric(
                precision=18,
                scale=4,
            ),
            nullable=False,
        ),
        sa.Column(
            "movement_type",
            sa.Enum(
                "reserve",
                "release",
                "consume",
                name=(
                    "reservation_movement_type_enum"
                ),
                native_enum=False,
                create_constraint=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(
                timezone=True
            ),
            server_default=sa.text(
                "now()"
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name=(
                "ck_reservation_movement_"
                "quantity_positive"
            ),
        ),
        sa.CheckConstraint(
            (
                "movement_type IN ("
                "'reserve', "
                "'release', "
                "'consume'"
                ")"
            ),
            name=(
                "ck_reservation_movement_type"
            ),
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
            ],
            [
                "companies.id",
            ],
            name=(
                "fk_reservation_movements_"
                "company_id_companies"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "source_document_id",
                "source_document_line_id",
                "product_id",
                "warehouse_id",
            ],
            [
                "trade_document_lines.company_id",
                (
                    "trade_document_lines."
                    "trade_document_id"
                ),
                "trade_document_lines.id",
                "trade_document_lines.product_id",
                (
                    "trade_document_lines."
                    "warehouse_id"
                ),
            ],
            name=(
                "fk_reservation_movements_"
                "trade_document_line"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
    )

    op.create_index(
        "ix_reservation_movements_company_id",
        "reservation_movements",
        [
            "company_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_reservation_movements_product_id",
        "reservation_movements",
        [
            "product_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_reservation_movements_warehouse_id",
        "reservation_movements",
        [
            "warehouse_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_reservation_movements_source_document_id",
        "reservation_movements",
        [
            "source_document_id",
        ],
        unique=False,
    )

    op.create_index(
        (
            "ix_reservation_movements_"
            "source_document_line_id"
        ),
        "reservation_movements",
        [
            "source_document_line_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_reservation_movements_stock",
        "reservation_movements",
        [
            "company_id",
            "product_id",
            "warehouse_id",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        (
            "ix_reservation_movements_"
            "source_line"
        ),
        "reservation_movements",
        [
            "company_id",
            "source_document_line_id",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        (
            "ix_reservation_movements_"
            "source_document"
        ),
        "reservation_movements",
        [
            "company_id",
            "source_document_id",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    # Drop the dependent table first. Only then may the
    # referenced candidate key be removed.
    op.drop_table(
        "reservation_movements"
    )

    op.drop_constraint(
        "uq_trade_document_lines_reservation_source",
        "trade_document_lines",
        type_="unique",
    )
