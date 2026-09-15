"""add purchase landed cost foundation

Revision ID: b9d6f2a4c713
Revises: a8c5e1d4f902
"""

from alembic import op
import sqlalchemy as sa


revision = "b9d6f2a4c713"
down_revision = "a8c5e1d4f902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_landed_cost_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("trade_document_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_document_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("cost_date", sa.Date(), nullable=False),
        sa.Column("reason_code", sa.String(50), nullable=True),
        sa.Column("reversal_of_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_plce_amount_positive",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_plce_currency_length",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR char_length(trim(reason_code)) > 0",
            name="ck_plce_reason_nonempty",
        ),
        sa.CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name="ck_plce_not_self_reversal",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_plce_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_plce_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "trade_document_id"],
            ["trade_documents.company_id", "trade_documents.id"],
            name="fk_plce_trade_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_document_id"],
            ["documents.id"],
            name="fk_plce_warehouse_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "reversal_of_id"],
            [
                "purchase_landed_cost_events.company_id",
                "purchase_landed_cost_events.id",
            ],
            name="fk_plce_reversal_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_plce_company_id_id",
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_plce_reversal_of",
        ),
    )

    op.create_index(
        "ix_plce_receipt",
        "purchase_landed_cost_events",
        [
            "company_id",
            "warehouse_document_id",
            "cost_date",
            "id",
        ],
    )
    op.create_index(
        "ix_plce_company",
        "purchase_landed_cost_events",
        ["company_id"],
    )
    op.create_index(
        "ix_plce_trade_doc",
        "purchase_landed_cost_events",
        ["trade_document_id"],
    )
    op.create_index(
        "ix_plce_wh_doc",
        "purchase_landed_cost_events",
        ["warehouse_document_id"],
    )
    op.create_index(
        "ix_plce_cost_date",
        "purchase_landed_cost_events",
        ["cost_date"],
    )
    op.create_index(
        "ix_plce_reversal",
        "purchase_landed_cost_events",
        ["reversal_of_id"],
    )

    op.create_table(
        "purchase_landed_cost_allocation_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("landed_cost_event_id", sa.Integer(), nullable=False),
        sa.Column("trade_fulfillment_line_id", sa.Integer(), nullable=False),
        sa.Column("trade_document_id", sa.Integer(), nullable=False),
        sa.Column("trade_document_line_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_document_line_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("allocated_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "allocated_amount > 0",
            name="ck_plca_amount_positive",
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_plca_quantity_positive",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "landed_cost_event_id"],
            [
                "purchase_landed_cost_events.company_id",
                "purchase_landed_cost_events.id",
            ],
            name="fk_plca_landed_cost_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trade_fulfillment_line_id"],
            ["trade_fulfillment_lines.id"],
            name="fk_plca_fulfillment_line",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "landed_cost_event_id",
            "trade_fulfillment_line_id",
            name="uq_plca_event_fulfillment_line",
        ),
    )

    op.create_index(
        "ix_plca_company",
        "purchase_landed_cost_allocation_events",
        ["company_id"],
    )
    op.create_index(
        "ix_plca_event",
        "purchase_landed_cost_allocation_events",
        ["landed_cost_event_id"],
    )
    op.create_index(
        "ix_plca_fline",
        "purchase_landed_cost_allocation_events",
        ["trade_fulfillment_line_id"],
    )
    op.create_index(
        "ix_plca_trade_doc",
        "purchase_landed_cost_allocation_events",
        ["trade_document_id"],
    )
    op.create_index(
        "ix_plca_trade_line",
        "purchase_landed_cost_allocation_events",
        ["trade_document_line_id"],
    )
    op.create_index(
        "ix_plca_wh_line",
        "purchase_landed_cost_allocation_events",
        ["warehouse_document_line_id"],
    )
    op.create_index(
        "ix_plca_product",
        "purchase_landed_cost_allocation_events",
        ["product_id"],
    )
    op.create_index(
        "ix_plca_warehouse",
        "purchase_landed_cost_allocation_events",
        ["warehouse_id"],
    )

    op.create_index(
        "ix_plca_source",
        "purchase_landed_cost_allocation_events",
        ["company_id", "landed_cost_event_id", "id"],
    )
    op.create_index(
        "ix_plca_fulfillment",
        "purchase_landed_cost_allocation_events",
        ["company_id", "trade_fulfillment_line_id", "id"],
    )


def downgrade() -> None:
    op.drop_table("purchase_landed_cost_allocation_events")
    op.drop_table("purchase_landed_cost_events")
