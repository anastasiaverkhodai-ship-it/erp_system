"""add sales line discount snapshot

Revision ID: f7b4d9e2c835
Revises: e6a3c8f1b724
"""

from alembic import op
import sqlalchemy as sa


revision = "f7b4d9e2c835"
down_revision = "e6a3c8f1b724"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trade_document_lines",
        sa.Column(
            "base_unit_price",
            sa.Numeric(18, 4),
            nullable=True,
        ),
    )
    op.add_column(
        "trade_document_lines",
        sa.Column(
            "discount_percent",
            sa.Numeric(9, 4),
            nullable=True,
        ),
    )
    op.add_column(
        "trade_document_lines",
        sa.Column(
            "discount_amount_per_unit",
            sa.Numeric(18, 4),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        "ck_trade_document_line_discount_state",
        "trade_document_lines",
        """
        (
            base_unit_price IS NULL
            AND discount_percent IS NULL
            AND discount_amount_per_unit IS NULL
        )
        OR
        (
            base_unit_price IS NOT NULL
            AND (
                (
                    discount_percent IS NOT NULL
                    AND discount_amount_per_unit IS NULL
                )
                OR
                (
                    discount_percent IS NULL
                    AND discount_amount_per_unit IS NOT NULL
                )
            )
        )
        """,
    )

    op.create_check_constraint(
        "ck_trade_document_line_base_unit_price_nonnegative",
        "trade_document_lines",
        """
        base_unit_price IS NULL
        OR base_unit_price >= 0
        """,
    )

    op.create_check_constraint(
        "ck_trade_document_line_discount_percent_range",
        "trade_document_lines",
        """
        discount_percent IS NULL
        OR (
            discount_percent >= 0
            AND discount_percent <= 100
        )
        """,
    )

    op.create_check_constraint(
        "ck_trade_document_line_discount_amount_nonnegative",
        "trade_document_lines",
        """
        discount_amount_per_unit IS NULL
        OR discount_amount_per_unit >= 0
        """,
    )

    op.create_check_constraint(
        "ck_trade_document_line_discount_amount_not_above_base",
        "trade_document_lines",
        """
        discount_amount_per_unit IS NULL
        OR (
            base_unit_price IS NOT NULL
            AND discount_amount_per_unit <= base_unit_price
        )
        """,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_trade_document_line_discount_amount_not_above_base",
        "trade_document_lines",
        type_="check",
    )
    op.drop_constraint(
        "ck_trade_document_line_discount_amount_nonnegative",
        "trade_document_lines",
        type_="check",
    )
    op.drop_constraint(
        "ck_trade_document_line_discount_percent_range",
        "trade_document_lines",
        type_="check",
    )
    op.drop_constraint(
        "ck_trade_document_line_base_unit_price_nonnegative",
        "trade_document_lines",
        type_="check",
    )
    op.drop_constraint(
        "ck_trade_document_line_discount_state",
        "trade_document_lines",
        type_="check",
    )

    op.drop_column(
        "trade_document_lines",
        "discount_amount_per_unit",
    )
    op.drop_column(
        "trade_document_lines",
        "discount_percent",
    )
    op.drop_column(
        "trade_document_lines",
        "base_unit_price",
    )
