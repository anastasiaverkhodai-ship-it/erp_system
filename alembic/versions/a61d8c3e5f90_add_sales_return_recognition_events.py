"""add sales return recognition events

Revision ID: a61d8c3e5f90
Revises: e3f7c9a1b624
"""

from typing import (
    Sequence,
    Union,
)

from alembic import op
import sqlalchemy as sa


revision: str = "a61d8c3e5f90"

down_revision: Union[
    str,
    Sequence[str],
    None,
] = "e3f7c9a1b624"

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
        "sales_return_recognition_events",
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
            "sales_recognition_event_id",
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
                length=3
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
                timezone=True
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
                "ck_srre_event_"
                "returned_quantity_positive"
            ),
        ),
        sa.CheckConstraint(
            "returned_gross_amount > 0",
            name=(
                "ck_srre_event_"
                "returned_gross_positive"
            ),
        ),
        sa.CheckConstraint(
            "returned_tax_amount >= 0",
            name=(
                "ck_srre_event_"
                "returned_tax_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            (
                "returned_tax_amount "
                "<= returned_gross_amount"
            ),
            name=(
                "ck_srre_event_"
                "tax_not_above_gross"
            ),
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_srre_event_"
                "currency_length"
            ),
        ),
        sa.CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_srre_event_"
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
                "fk_srre_event_company_trade_return"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "sales_recognition_event_id",
            ],
            [
                "sales_recognition_events.company_id",
                "sales_recognition_events.id",
            ],
            name=(
                "fk_srre_event_"
                "company_sales_recognition"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "trade_return_event_id",
                "sales_recognition_event_id",
            ],
            [
                "sales_return_recognition_events.company_id",
                "sales_return_recognition_events.id",
                (
                    "sales_return_recognition_events."
                    "trade_return_event_id"
                ),
                (
                    "sales_return_recognition_events."
                    "sales_recognition_event_id"
                ),
            ],
            name=(
                "fk_srre_event_reversal_source"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_srre_event_company_id_id"
            ),
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            "trade_return_event_id",
            "sales_recognition_event_id",
            name=(
                "uq_srre_event_"
                "company_id_id_source"
            ),
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_srre_event_reversal_of"
            ),
        ),
    )

    op.create_index(
        "ix_srre_event_pair_history",
        "sales_return_recognition_events",
        [
            "company_id",
            "trade_return_event_id",
            "sales_recognition_event_id",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_srre_event_trade_return",
        "sales_return_recognition_events",
        [
            "company_id",
            "trade_return_event_id",
            "recognition_date",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_srre_event_sales_recognition",
        "sales_return_recognition_events",
        [
            "company_id",
            "sales_recognition_event_id",
            "recognition_date",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_srre_event_sales_recognition",
        table_name=(
            "sales_return_recognition_events"
        ),
    )

    op.drop_index(
        "ix_srre_event_trade_return",
        table_name=(
            "sales_return_recognition_events"
        ),
    )

    op.drop_index(
        "ix_srre_event_pair_history",
        table_name=(
            "sales_return_recognition_events"
        ),
    )

    op.drop_table(
        "sales_return_recognition_events"
    )
