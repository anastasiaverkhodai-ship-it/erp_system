"""add warehouse document posting foundation

Revision ID: f914fcaf6a2c
Revises: 2beb07811e15
Create Date: 2026-08-17 14:38:30.564778

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f914fcaf6a2c"
down_revision: Union[str, Sequence[str], None] = "2beb07811e15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # ---------------------------------------------------------
    # DOCUMENT LINES
    # ---------------------------------------------------------

    op.create_table(
        "document_lines",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "document_id",
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
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.Column(
            "price",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_lines_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_document_lines_product_id_products",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
            name="fk_document_lines_warehouse_id_warehouses",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_document_lines_document_id"),
        "document_lines",
        ["document_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_document_lines_product_id"),
        "document_lines",
        ["product_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_document_lines_warehouse_id"),
        "document_lines",
        ["warehouse_id"],
        unique=False,
    )

    # ---------------------------------------------------------
    # DOCUMENTS
    # ---------------------------------------------------------

    op.add_column(
        "documents",
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
    )

    op.add_column(
        "documents",
        sa.Column(
            "number",
            sa.String(length=50),
            nullable=False,
        ),
    )

    op.add_column(
        "documents",
        sa.Column(
            "document_date",
            sa.Date(),
            nullable=False,
        ),
    )

    op.add_column(
        "documents",
        sa.Column(
            "created_by",
            sa.Integer(),
            nullable=False,
        ),
    )

    op.add_column(
        "documents",
        sa.Column(
            "posted_at",
            sa.DateTime(),
            nullable=True,
        ),
    )

    op.alter_column(
        "documents",
        "document_type",
        existing_type=sa.VARCHAR(length=100),
        type_=sa.Enum(
            "receipt",
            "issue",
            "adjustment",
            name="document_type_enum",
            native_enum=False,
        ),
        existing_nullable=False,
    )

    op.alter_column(
        "documents",
        "status",
        existing_type=sa.VARCHAR(length=50),
        type_=sa.Enum(
            "draft",
            "posted",
            "cancelled",
            name="document_status_enum",
            native_enum=False,
        ),
        existing_nullable=False,
    )

    op.create_index(
        op.f("ix_documents_company_id"),
        "documents",
        ["company_id"],
        unique=False,
    )

    op.create_unique_constraint(
        "uq_document_company_number",
        "documents",
        ["company_id", "number"],
    )

    op.create_foreign_key(
        "fk_documents_company_id_companies",
        "documents",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_documents_created_by_users",
        "documents",
        "users",
        ["created_by"],
        ["id"],
        ondelete="RESTRICT",
    )

    # ---------------------------------------------------------
    # PRODUCTS
    # ---------------------------------------------------------

    op.add_column(
        "products",
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
    )

    op.drop_constraint(
        op.f("products_sku_key"),
        "products",
        type_="unique",
    )

    op.create_index(
        op.f("ix_products_company_id"),
        "products",
        ["company_id"],
        unique=False,
    )

    op.create_unique_constraint(
        "uq_product_company_sku",
        "products",
        ["company_id", "sku"],
    )

    op.create_foreign_key(
        "fk_products_company_id_companies",
        "products",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # ---------------------------------------------------------
    # STOCK LEDGER
    # ---------------------------------------------------------

    op.add_column(
        "stock_ledger",
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
    )

    op.add_column(
        "stock_ledger",
        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
        ),
    )

    op.add_column(
        "stock_ledger",
        sa.Column(
            "document_line_id",
            sa.Integer(),
            nullable=False,
        ),
    )

    op.add_column(
        "stock_ledger",
        sa.Column(
            "movement_type",
            sa.Enum(
                "receipt",
                "issue",
                "adjustment",
                name="stock_movement_type_enum",
                native_enum=False,
            ),
            nullable=False,
        ),
    )

    op.add_column(
        "stock_ledger",
        sa.Column(
            "movement_date",
            sa.Date(),
            nullable=False,
        ),
    )

    op.alter_column(
        "stock_ledger",
        "quantity",
        existing_type=sa.NUMERIC(
            precision=15,
            scale=3,
        ),
        type_=sa.Numeric(
            precision=18,
            scale=4,
        ),
        existing_nullable=False,
    )

    op.create_index(
        op.f("ix_stock_ledger_company_id"),
        "stock_ledger",
        ["company_id"],
        unique=False,
    )

    op.create_index(
        "ix_stock_ledger_company_product_warehouse",
        "stock_ledger",
        [
            "company_id",
            "product_id",
            "warehouse_id",
        ],
        unique=False,
    )

    op.create_index(
        op.f("ix_stock_ledger_document_id"),
        "stock_ledger",
        ["document_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_stock_ledger_document_line_id"),
        "stock_ledger",
        ["document_line_id"],
        unique=False,
    )

    # Remove old foreign keys so we can recreate them
    # with explicit ON DELETE behavior and stable names.

    op.drop_constraint(
        op.f("stock_ledger_warehouse_id_fkey"),
        "stock_ledger",
        type_="foreignkey",
    )

    op.drop_constraint(
        op.f("stock_ledger_product_id_fkey"),
        "stock_ledger",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "fk_stock_ledger_product_id_products",
        "stock_ledger",
        "products",
        ["product_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_stock_ledger_company_id_companies",
        "stock_ledger",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_stock_ledger_warehouse_id_warehouses",
        "stock_ledger",
        "warehouses",
        ["warehouse_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_stock_ledger_document_line_id_document_lines",
        "stock_ledger",
        "document_lines",
        ["document_line_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_stock_ledger_document_id_documents",
        "stock_ledger",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # ---------------------------------------------------------
    # WAREHOUSES
    # ---------------------------------------------------------

    op.add_column(
        "warehouses",
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
    )

    op.add_column(
        "warehouses",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
        ),
    )

    op.create_index(
        op.f("ix_warehouses_company_id"),
        "warehouses",
        ["company_id"],
        unique=False,
    )

    op.create_unique_constraint(
        "uq_warehouse_company_name",
        "warehouses",
        ["company_id", "name"],
    )

    op.create_foreign_key(
        "fk_warehouses_company_id_companies",
        "warehouses",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Downgrade schema."""

    # ---------------------------------------------------------
    # WAREHOUSES
    # ---------------------------------------------------------

    op.drop_constraint(
        "fk_warehouses_company_id_companies",
        "warehouses",
        type_="foreignkey",
    )

    op.drop_constraint(
        "uq_warehouse_company_name",
        "warehouses",
        type_="unique",
    )

    op.drop_index(
        op.f("ix_warehouses_company_id"),
        table_name="warehouses",
    )

    op.drop_column(
        "warehouses",
        "is_active",
    )

    op.drop_column(
        "warehouses",
        "company_id",
    )

    # ---------------------------------------------------------
    # STOCK LEDGER
    # ---------------------------------------------------------

    op.drop_constraint(
        "fk_stock_ledger_product_id_products",
        "stock_ledger",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_stock_ledger_company_id_companies",
        "stock_ledger",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_stock_ledger_warehouse_id_warehouses",
        "stock_ledger",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_stock_ledger_document_line_id_document_lines",
        "stock_ledger",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_stock_ledger_document_id_documents",
        "stock_ledger",
        type_="foreignkey",
    )

    op.create_foreign_key(
        op.f("stock_ledger_product_id_fkey"),
        "stock_ledger",
        "products",
        ["product_id"],
        ["id"],
    )

    op.create_foreign_key(
        op.f("stock_ledger_warehouse_id_fkey"),
        "stock_ledger",
        "warehouses",
        ["warehouse_id"],
        ["id"],
    )

    op.drop_index(
        op.f("ix_stock_ledger_document_line_id"),
        table_name="stock_ledger",
    )

    op.drop_index(
        op.f("ix_stock_ledger_document_id"),
        table_name="stock_ledger",
    )

    op.drop_index(
        "ix_stock_ledger_company_product_warehouse",
        table_name="stock_ledger",
    )

    op.drop_index(
        op.f("ix_stock_ledger_company_id"),
        table_name="stock_ledger",
    )

    op.alter_column(
        "stock_ledger",
        "quantity",
        existing_type=sa.Numeric(
            precision=18,
            scale=4,
        ),
        type_=sa.NUMERIC(
            precision=15,
            scale=3,
        ),
        existing_nullable=False,
    )

    op.drop_column(
        "stock_ledger",
        "movement_date",
    )

    op.drop_column(
        "stock_ledger",
        "movement_type",
    )

    op.drop_column(
        "stock_ledger",
        "document_line_id",
    )

    op.drop_column(
        "stock_ledger",
        "document_id",
    )

    op.drop_column(
        "stock_ledger",
        "company_id",
    )

    # ---------------------------------------------------------
    # PRODUCTS
    # ---------------------------------------------------------

    op.drop_constraint(
        "fk_products_company_id_companies",
        "products",
        type_="foreignkey",
    )

    op.drop_constraint(
        "uq_product_company_sku",
        "products",
        type_="unique",
    )

    op.drop_index(
        op.f("ix_products_company_id"),
        table_name="products",
    )

    op.create_unique_constraint(
        op.f("products_sku_key"),
        "products",
        ["sku"],
        postgresql_nulls_not_distinct=False,
    )

    op.drop_column(
        "products",
        "company_id",
    )

    # ---------------------------------------------------------
    # DOCUMENTS
    # ---------------------------------------------------------

    op.drop_constraint(
        "fk_documents_created_by_users",
        "documents",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_documents_company_id_companies",
        "documents",
        type_="foreignkey",
    )

    op.drop_constraint(
        "uq_document_company_number",
        "documents",
        type_="unique",
    )

    op.drop_index(
        op.f("ix_documents_company_id"),
        table_name="documents",
    )

    op.alter_column(
        "documents",
        "status",
        existing_type=sa.Enum(
            "draft",
            "posted",
            "cancelled",
            name="document_status_enum",
            native_enum=False,
        ),
        type_=sa.VARCHAR(length=50),
        existing_nullable=False,
    )

    op.alter_column(
        "documents",
        "document_type",
        existing_type=sa.Enum(
            "receipt",
            "issue",
            "adjustment",
            name="document_type_enum",
            native_enum=False,
        ),
        type_=sa.VARCHAR(length=100),
        existing_nullable=False,
    )

    op.drop_column(
        "documents",
        "posted_at",
    )

    op.drop_column(
        "documents",
        "created_by",
    )

    op.drop_column(
        "documents",
        "document_date",
    )

    op.drop_column(
        "documents",
        "number",
    )

    op.drop_column(
        "documents",
        "company_id",
    )

    # ---------------------------------------------------------
    # DOCUMENT LINES
    # ---------------------------------------------------------

    op.drop_index(
        op.f("ix_document_lines_warehouse_id"),
        table_name="document_lines",
    )

    op.drop_index(
        op.f("ix_document_lines_product_id"),
        table_name="document_lines",
    )

    op.drop_index(
        op.f("ix_document_lines_document_id"),
        table_name="document_lines",
    )

    op.drop_table(
        "document_lines",
    )