"""add invoice fulfillment allocations

Revision ID: b0a3b206bd82
Revises: e4a8c1d2f9b6
Create Date: 2026-08-29 11:38:08.203241

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b0a3b206bd82'
down_revision: Union[str, Sequence[str], None] = 'e4a8c1d2f9b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # Composite UNIQUE identities must exist before PostgreSQL
    # can create the allocation table foreign keys that reference
    # these exact column sets.
    op.create_unique_constraint(
        "uq_trade_document_lines_invoice_matching_source",
        "trade_document_lines",
        [
            "company_id",
            "trade_document_id",
            "id",
            "product_id",
        ],
    )

    op.create_unique_constraint(
        "uq_trade_fulfillment_lines_invoice_matching_target",
        "trade_fulfillment_lines",
        [
            "company_id",
            "fulfillment_id",
            "trade_document_id",
            "trade_document_line_id",
            "id",
            "product_id",
        ],
    )

    op.create_table(
        "invoice_fulfillment_allocations",
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
            "invoice_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "invoice_line_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "fulfillment_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "fulfillment_line_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "order_line_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "product_id",
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
            "status",
            sa.Enum(
                "active",
                "reversed",
                name=(
                    "invoice_fulfillment_"
                    "allocation_status_enum"
                ),
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(
                timezone=True,
            ),
            server_default=sa.text(
                "now()"
            ),
            nullable=False,
        ),
        sa.Column(
            "reversed_by",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "reversed_at",
            sa.DateTime(
                timezone=True,
            ),
            nullable=True,
        ),
        sa.CheckConstraint(
            "("
            "status = 'active' "
            "AND reversed_by IS NULL "
            "AND reversed_at IS NULL"
            ") OR ("
            "status = 'reversed' "
            "AND reversed_by IS NOT NULL "
            "AND reversed_at IS NOT NULL"
            ")",
            name=(
                "ck_invoice_fulfillment_allocations_"
                "reversal_state"
            ),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'reversed')",
            name=(
                "ck_invoice_fulfillment_allocations_"
                "status"
            ),
        ),
        sa.CheckConstraint(
            "invoice_id <> order_id",
            name=(
                "ck_invoice_fulfillment_allocations_"
                "different_documents"
            ),
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name=(
                "ck_invoice_fulfillment_allocations_"
                "quantity_positive"
            ),
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "fulfillment_id",
                "order_id",
                "order_line_id",
                "fulfillment_line_id",
                "product_id",
            ],
            [
                "trade_fulfillment_lines.company_id",
                "trade_fulfillment_lines.fulfillment_id",
                "trade_fulfillment_lines.trade_document_id",
                "trade_fulfillment_lines.trade_document_line_id",
                "trade_fulfillment_lines.id",
                "trade_fulfillment_lines.product_id",
            ],
            name=(
                "fk_invoice_fulfillment_allocations_"
                "fulfillment_line"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "invoice_id",
                "invoice_line_id",
                "product_id",
            ],
            [
                "trade_document_lines.company_id",
                "trade_document_lines.trade_document_id",
                "trade_document_lines.id",
                "trade_document_lines.product_id",
            ],
            name=(
                "fk_invoice_fulfillment_allocations_"
                "invoice_line"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id"
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_invoice_fulfillment_allocations_"
                "company_id_id"
            ),
        ),
    )

    op.create_index(
        "ix_if_alloc_fulfillment_active",
        "invoice_fulfillment_allocations",
        [
            "company_id",
            "fulfillment_line_id",
            "id",
        ],
        unique=False,
        postgresql_where=sa.text(
            "status = 'active'"
        ),
    )

    op.create_index(
        "ix_if_alloc_invoice_active",
        "invoice_fulfillment_allocations",
        [
            "company_id",
            "invoice_line_id",
            "id",
        ],
        unique=False,
        postgresql_where=sa.text(
            "status = 'active'"
        ),
    )

    for column_name in (
        "company_id",
        "fulfillment_id",
        "fulfillment_line_id",
        "invoice_id",
        "invoice_line_id",
        "order_id",
        "order_line_id",
        "product_id",
        "status",
    ):
        op.create_index(
            op.f(
                "ix_invoice_fulfillment_allocations_"
                + column_name
            ),
            "invoice_fulfillment_allocations",
            [column_name],
            unique=False,
        )

    op.create_index(
        "uq_if_alloc_active_pair",
        "invoice_fulfillment_allocations",
        [
            "company_id",
            "invoice_line_id",
            "fulfillment_line_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active'"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""

    # Drop the dependent allocation table first. Its foreign keys
    # depend on the composite UNIQUE identities below.
    op.drop_index(
        "uq_if_alloc_active_pair",
        table_name=(
            "invoice_fulfillment_allocations"
        ),
        postgresql_where=sa.text(
            "status = 'active'"
        ),
    )

    for column_name in reversed(
        (
            "company_id",
            "fulfillment_id",
            "fulfillment_line_id",
            "invoice_id",
            "invoice_line_id",
            "order_id",
            "order_line_id",
            "product_id",
            "status",
        )
    ):
        op.drop_index(
            op.f(
                "ix_invoice_fulfillment_allocations_"
                + column_name
            ),
            table_name=(
                "invoice_fulfillment_allocations"
            ),
        )

    op.drop_index(
        "ix_if_alloc_invoice_active",
        table_name=(
            "invoice_fulfillment_allocations"
        ),
        postgresql_where=sa.text(
            "status = 'active'"
        ),
    )

    op.drop_index(
        "ix_if_alloc_fulfillment_active",
        table_name=(
            "invoice_fulfillment_allocations"
        ),
        postgresql_where=sa.text(
            "status = 'active'"
        ),
    )

    op.drop_table(
        "invoice_fulfillment_allocations"
    )

    op.drop_constraint(
        "uq_trade_fulfillment_lines_invoice_matching_target",
        "trade_fulfillment_lines",
        type_="unique",
    )

    op.drop_constraint(
        "uq_trade_document_lines_invoice_matching_source",
        "trade_document_lines",
        type_="unique",
    )
