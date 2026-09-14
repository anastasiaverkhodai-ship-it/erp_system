"""add sales price provenance

Revision ID: e6a3c8f1b724
Revises: d4f2a7c9e613

Persist the resolved master-price provenance on TradeDocumentLine.

unit_price remains the canonical immutable commercial snapshot.

Manual pricing:
    all provenance columns are NULL.

Master pricing:
    all provenance columns are NOT NULL.

No discount, FX, UOM conversion, automatic repricing,
accounting, VAT, AR, warehouse or settlement behavior is added.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e6a3c8f1b724"
down_revision: str | None = "d4f2a7c9e613"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trade_document_lines",
        sa.Column(
            "price_type_code",
            sa.String(length=100),
            nullable=True,
        ),
    )
    op.add_column(
        "trade_document_lines",
        sa.Column(
            "source_product_price_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "trade_document_lines",
        sa.Column(
            "price_uom_code",
            sa.String(length=50),
            nullable=True,
        ),
    )
    op.add_column(
        "trade_document_lines",
        sa.Column(
            "price_effective_from",
            sa.Date(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_trade_document_lines_company_price_type",
        "trade_document_lines",
        "price_types",
        ["company_id", "price_type_code"],
        ["company_id", "code"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_trade_document_lines_company_source_product_price",
        "trade_document_lines",
        "product_prices",
        ["company_id", "source_product_price_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_trade_document_line_price_provenance_state",
        "trade_document_lines",
        """
        (
            price_type_code IS NULL
            AND source_product_price_id IS NULL
            AND price_uom_code IS NULL
            AND price_effective_from IS NULL
        )
        OR
        (
            price_type_code IS NOT NULL
            AND source_product_price_id IS NOT NULL
            AND price_uom_code IS NOT NULL
            AND price_effective_from IS NOT NULL
        )
        """,
    )
    op.create_check_constraint(
        "ck_trade_document_line_price_type_code_nonempty",
        "trade_document_lines",
        (
            "price_type_code IS NULL OR "
            "length(trim(price_type_code)) > 0"
        ),
    )
    op.create_check_constraint(
        "ck_trade_document_line_price_uom_code_nonempty",
        "trade_document_lines",
        (
            "price_uom_code IS NULL OR "
            "length(trim(price_uom_code)) > 0"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_trade_document_line_price_uom_code_nonempty",
        "trade_document_lines",
        type_="check",
    )
    op.drop_constraint(
        "ck_trade_document_line_price_type_code_nonempty",
        "trade_document_lines",
        type_="check",
    )
    op.drop_constraint(
        "ck_trade_document_line_price_provenance_state",
        "trade_document_lines",
        type_="check",
    )
    op.drop_constraint(
        "fk_trade_document_lines_company_source_product_price",
        "trade_document_lines",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_trade_document_lines_company_price_type",
        "trade_document_lines",
        type_="foreignkey",
    )

    op.drop_column(
        "trade_document_lines",
        "price_effective_from",
    )
    op.drop_column(
        "trade_document_lines",
        "price_uom_code",
    )
    op.drop_column(
        "trade_document_lines",
        "source_product_price_id",
    )
    op.drop_column(
        "trade_document_lines",
        "price_type_code",
    )
