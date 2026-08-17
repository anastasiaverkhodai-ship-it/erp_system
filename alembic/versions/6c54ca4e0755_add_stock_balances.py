"""add stock balances

Revision ID: 6c54ca4e0755
Revises: f914fcaf6a2c
Create Date: 2026-08-17 18:32:49.772726

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6c54ca4e0755"
down_revision: Union[str, Sequence[str], None] = "f914fcaf6a2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # ---------------------------------------------------------
    # STOCK BALANCES
    # ---------------------------------------------------------

    op.create_table(
        "stock_balances",
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
            "quantity",
            sa.Numeric(
                precision=18,
                scale=4,
            ),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_stock_balances_company_id_companies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_stock_balances_product_id_products",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
            name="fk_stock_balances_warehouse_id_warehouses",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "product_id",
            "warehouse_id",
            name="uq_stock_balance_company_product_warehouse",
        ),
    )

    op.create_index(
        op.f("ix_stock_balances_company_id"),
        "stock_balances",
        ["company_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_stock_balances_product_id"),
        "stock_balances",
        ["product_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_stock_balances_warehouse_id"),
        "stock_balances",
        ["warehouse_id"],
        unique=False,
    )

    # ---------------------------------------------------------
    # BACKFILL CURRENT BALANCES FROM STOCK LEDGER
    # ---------------------------------------------------------

    op.execute(
        """
        INSERT INTO stock_balances (
            company_id,
            product_id,
            warehouse_id,
            quantity,
            updated_at
        )
        SELECT
            company_id,
            product_id,
            warehouse_id,
            SUM(quantity),
            CURRENT_TIMESTAMP
        FROM stock_ledger
        GROUP BY
            company_id,
            product_id,
            warehouse_id
        """
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index(
        op.f("ix_stock_balances_warehouse_id"),
        table_name="stock_balances",
    )

    op.drop_index(
        op.f("ix_stock_balances_product_id"),
        table_name="stock_balances",
    )

    op.drop_index(
        op.f("ix_stock_balances_company_id"),
        table_name="stock_balances",
    )

    op.drop_table(
        "stock_balances",
    )