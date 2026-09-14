"""add persistent pricing master

Revision ID: c9e1f4a6b802
Revises: b8e0f2a4d357
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9e1f4a6b802"
down_revision: Union[str, None] = "b8e0f2a4d357"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column(
            "currency_code",
            sa.String(length=3),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(code)) > 0",
            name="ck_price_types_code_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_price_types_name_nonempty",
        ),
        sa.CheckConstraint(
            "kind IN ('sales', 'purchase', 'internal')",
            name="ck_price_types_kind",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_price_types_currency_code_length",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_price_types_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "code",
            name="uq_price_types_company_code",
        ),
    )

    op.create_index(
        "ix_price_types_company_id",
        "price_types",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_price_types_kind",
        "price_types",
        ["kind"],
        unique=False,
    )
    op.create_index(
        "ix_price_types_currency_code",
        "price_types",
        ["currency_code"],
        unique=False,
    )

    op.create_table(
        "product_prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column(
            "price_type_code",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "amount",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.Column(
            "uom_code",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "effective_from",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount >= 0",
            name="ck_product_prices_amount_nonnegative",
        ),
        sa.CheckConstraint(
            "length(trim(uom_code)) > 0",
            name="ck_product_prices_uom_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "product_id"],
            ["products.company_id", "products.id"],
            name="fk_product_prices_company_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "price_type_code"],
            ["price_types.company_id", "price_types.code"],
            name="fk_product_prices_company_price_type",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_product_prices_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "product_id",
            "price_type_code",
            "uom_code",
            "effective_from",
            name="uq_product_prices_effective_identity",
        ),
    )

    op.create_index(
        "ix_product_prices_company_id",
        "product_prices",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_product_prices_product_id",
        "product_prices",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "ix_product_prices_price_type_code",
        "product_prices",
        ["price_type_code"],
        unique=False,
    )
    op.create_index(
        "ix_product_prices_effective_from",
        "product_prices",
        ["effective_from"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_prices_effective_from",
        table_name="product_prices",
    )
    op.drop_index(
        "ix_product_prices_price_type_code",
        table_name="product_prices",
    )
    op.drop_index(
        "ix_product_prices_product_id",
        table_name="product_prices",
    )
    op.drop_index(
        "ix_product_prices_company_id",
        table_name="product_prices",
    )
    op.drop_table("product_prices")

    op.drop_index(
        "ix_price_types_currency_code",
        table_name="price_types",
    )
    op.drop_index(
        "ix_price_types_kind",
        table_name="price_types",
    )
    op.drop_index(
        "ix_price_types_company_id",
        table_name="price_types",
    )
    op.drop_table("price_types")
