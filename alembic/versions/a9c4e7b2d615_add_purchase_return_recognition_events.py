"""add purchase return recognition events

Revision ID: a9c4e7b2d615
Revises: f2c6a8d1e704
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9c4e7b2d615"
down_revision: Union[
    str,
    Sequence[str],
    None,
] = "f2c6a8d1e704"
branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None
depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


def upgrade() -> None:
    op.create_table(
        "purchase_return_recognition_events",
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
            "trade_return_event_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "invoice_fulfillment_allocation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "recognition_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "returned_quantity",
            sa.Numeric(
                precision=18,
                scale=4,
            ),
            nullable=False,
        ),
        sa.Column(
            "returned_base_amount",
            sa.Numeric(
                precision=18,
                scale=2,
            ),
            nullable=False,
        ),
        sa.Column(
            "returned_gross_amount",
            sa.Numeric(
                precision=18,
                scale=2,
            ),
            nullable=False,
        ),
        sa.Column(
            "returned_tax_amount",
            sa.Numeric(
                precision=18,
                scale=2,
            ),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(
                length=3,
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
            "reversal_of_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.CheckConstraint(
            "returned_quantity > 0",
            name=(
                "ck_prre_event_"
                "returned_quantity_positive"
            ),
        ),
        sa.CheckConstraint(
            "returned_base_amount >= 0",
            name=(
                "ck_prre_event_"
                "returned_base_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "returned_gross_amount >= 0",
            name=(
                "ck_prre_event_"
                "returned_gross_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "returned_tax_amount >= 0",
            name=(
                "ck_prre_event_"
                "returned_tax_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            (
                "returned_tax_amount "
                "<= returned_gross_amount"
            ),
            name=(
                "ck_prre_event_"
                "tax_not_above_gross"
            ),
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_prre_event_"
                "currency_length"
            ),
        ),
        sa.CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_prre_event_"
                "not_self_reversal"
            ),
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
            [
                "company_id",
                "trade_return_event_id",
            ],
            [
                "trade_return_events.company_id",
                "trade_return_events.id",
            ],
            name=(
                "fk_prre_event_"
                "company_trade_return"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "invoice_fulfillment_allocation_id",
            ],
            [
                (
                    "invoice_fulfillment_allocations."
                    "company_id"
                ),
                (
                    "invoice_fulfillment_allocations."
                    "id"
                ),
            ],
            name=(
                "fk_prre_event_company_"
                "invoice_fulfillment_allocation"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "trade_return_event_id",
                "invoice_fulfillment_allocation_id",
            ],
            [
                (
                    "purchase_return_recognition_events."
                    "company_id"
                ),
                (
                    "purchase_return_recognition_events."
                    "id"
                ),
                (
                    "purchase_return_recognition_events."
                    "trade_return_event_id"
                ),
                (
                    "purchase_return_recognition_events."
                    "invoice_fulfillment_allocation_id"
                ),
            ],
            name="fk_prre_event_reversal_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_prre_event_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            "trade_return_event_id",
            "invoice_fulfillment_allocation_id",
            name=(
                "uq_prre_event_"
                "company_id_id_source"
            ),
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_prre_event_reversal_of",
        ),
    )

    op.create_index(
        "ix_prre_event_pair_history",
        "purchase_return_recognition_events",
        [
            "company_id",
            "trade_return_event_id",
            "invoice_fulfillment_allocation_id",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_prre_event_trade_return",
        "purchase_return_recognition_events",
        [
            "company_id",
            "trade_return_event_id",
            "recognition_date",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_prre_event_invoice_fulfillment",
        "purchase_return_recognition_events",
        [
            "company_id",
            "invoice_fulfillment_allocation_id",
            "recognition_date",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prre_event_invoice_fulfillment",
        table_name=(
            "purchase_return_recognition_events"
        ),
    )

    op.drop_index(
        "ix_prre_event_trade_return",
        table_name=(
            "purchase_return_recognition_events"
        ),
    )

    op.drop_index(
        "ix_prre_event_pair_history",
        table_name=(
            "purchase_return_recognition_events"
        ),
    )

    op.drop_table(
        "purchase_return_recognition_events"
    )
