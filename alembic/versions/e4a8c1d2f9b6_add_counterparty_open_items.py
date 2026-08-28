"""add counterparty open items

Revision ID: e4a8c1d2f9b6
Revises: 7f4c2a91d8e3
"""

from alembic import op
import sqlalchemy as sa


revision = "e4a8c1d2f9b6"
down_revision = "7f4c2a91d8e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "counterparty_open_items",
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
            "trade_document_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "counterparty_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "contract_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "item_type",
            sa.Enum(
                "receivable",
                "payable",
                name=(
                    "counterparty_open_item_type_enum"
                ),
                native_enum=False,
                create_constraint=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "partially_settled",
                "settled",
                "cancelled",
                name=(
                    "counterparty_open_item_status_enum"
                ),
                native_enum=False,
                create_constraint=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column(
            "document_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "due_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(length=3),
            nullable=False,
        ),
        sa.Column(
            "original_amount",
            sa.Numeric(
                precision=18,
                scale=2,
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
            (
                "item_type IN "
                "('receivable', 'payable')"
            ),
            name=(
                "ck_counterparty_open_item_type"
            ),
        ),
        sa.CheckConstraint(
            (
                "status IN ("
                "'open', "
                "'partially_settled', "
                "'settled', "
                "'cancelled'"
                ")"
            ),
            name=(
                "ck_counterparty_open_item_status"
            ),
        ),
        sa.CheckConstraint(
            "original_amount > 0",
            name=(
                "ck_counterparty_open_item_"
                "original_amount_positive"
            ),
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_counterparty_open_item_"
                "currency_code_length"
            ),
        ),
        sa.CheckConstraint(
            "due_date >= document_date",
            name=(
                "ck_counterparty_open_item_"
                "due_date_not_before_document_date"
            ),
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "counterparty_id",
            ],
            [
                "counterparties.company_id",
                "counterparties.id",
            ],
            name=(
                "fk_counterparty_open_items_"
                "company_counterparty"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "counterparty_id",
                "contract_id",
            ],
            [
                "contracts.company_id",
                "contracts.counterparty_id",
                "contracts.id",
            ],
            name=(
                "fk_counterparty_open_items_"
                "company_counterparty_contract"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "trade_document_id",
            ],
            [
                "trade_documents.company_id",
                "trade_documents.id",
            ],
            name=(
                "fk_counterparty_open_items_"
                "company_trade_document"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
            ],
            [
                "companies.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_counterparty_open_items_"
                "company_id_id"
            ),
        ),
        sa.UniqueConstraint(
            "company_id",
            "trade_document_id",
            name=(
                "uq_counterparty_open_items_"
                "company_trade_document"
            ),
        ),
    )

    op.create_index(
        op.f(
            "ix_counterparty_open_items_company_id"
        ),
        "counterparty_open_items",
        [
            "company_id",
        ],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_counterparty_open_items_trade_document_id"
        ),
        "counterparty_open_items",
        [
            "trade_document_id",
        ],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_counterparty_open_items_counterparty_id"
        ),
        "counterparty_open_items",
        [
            "counterparty_id",
        ],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_counterparty_open_items_contract_id"
        ),
        "counterparty_open_items",
        [
            "contract_id",
        ],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_counterparty_open_items_item_type"
        ),
        "counterparty_open_items",
        [
            "item_type",
        ],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_counterparty_open_items_status"
        ),
        "counterparty_open_items",
        [
            "status",
        ],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_counterparty_open_items_document_date"
        ),
        "counterparty_open_items",
        [
            "document_date",
        ],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_counterparty_open_items_due_date"
        ),
        "counterparty_open_items",
        [
            "due_date",
        ],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_counterparty_open_items_currency_code"
        ),
        "counterparty_open_items",
        [
            "currency_code",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f(
            "ix_counterparty_open_items_currency_code"
        ),
        table_name="counterparty_open_items",
    )

    op.drop_index(
        op.f(
            "ix_counterparty_open_items_due_date"
        ),
        table_name="counterparty_open_items",
    )

    op.drop_index(
        op.f(
            "ix_counterparty_open_items_document_date"
        ),
        table_name="counterparty_open_items",
    )

    op.drop_index(
        op.f(
            "ix_counterparty_open_items_status"
        ),
        table_name="counterparty_open_items",
    )

    op.drop_index(
        op.f(
            "ix_counterparty_open_items_item_type"
        ),
        table_name="counterparty_open_items",
    )

    op.drop_index(
        op.f(
            "ix_counterparty_open_items_contract_id"
        ),
        table_name="counterparty_open_items",
    )

    op.drop_index(
        op.f(
            "ix_counterparty_open_items_counterparty_id"
        ),
        table_name="counterparty_open_items",
    )

    op.drop_index(
        op.f(
            "ix_counterparty_open_items_trade_document_id"
        ),
        table_name="counterparty_open_items",
    )

    op.drop_index(
        op.f(
            "ix_counterparty_open_items_company_id"
        ),
        table_name="counterparty_open_items",
    )

    op.drop_table(
        "counterparty_open_items"
    )
