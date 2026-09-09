"""add purchase value correction allocation events

Revision ID: c0f1a7e9d432
Revises: bfbfb3452765
"""

from typing import (
    Sequence,
    Union,
)

from alembic import op
import sqlalchemy as sa


revision: str = "c0f1a7e9d432"
down_revision: Union[str, None] = "bfbfb3452765"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "purchase_value_correction_allocation_events",
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
            "trade_value_correction_event_id",
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
            "original_allocated_base_amount",
            sa.Numeric(
                precision=18,
                scale=2,
            ),
            nullable=False,
        ),
        sa.Column(
            "corrected_allocated_base_amount",
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
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "reversal_of_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.CheckConstraint(
            "original_allocated_base_amount >= 0",
            name=(
                "ck_pvca_event_"
                "original_base_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "corrected_allocated_base_amount >= 0",
            name=(
                "ck_pvca_event_"
                "corrected_base_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            (
                "original_allocated_base_amount "
                "<> corrected_allocated_base_amount"
            ),
            name="ck_pvca_event_not_noop",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_pvca_event_currency_length",
        ),
        sa.CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_pvca_event_not_self_reversal",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
            ],
            [
                "companies.id",
            ],
            name="fk_pvca_event_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "created_by",
            ],
            [
                "users.id",
            ],
            name="fk_pvca_event_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "trade_value_correction_event_id",
            ],
            [
                "trade_value_correction_events.company_id",
                "trade_value_correction_events.id",
            ],
            name=(
                "fk_pvca_event_"
                "trade_value_correction"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "invoice_fulfillment_allocation_id",
            ],
            [
                "invoice_fulfillment_allocations.company_id",
                "invoice_fulfillment_allocations.id",
            ],
            name=(
                "fk_pvca_event_"
                "invoice_fulfillment_allocation"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "trade_value_correction_event_id",
                "invoice_fulfillment_allocation_id",
            ],
            [
                (
                    "purchase_value_correction_"
                    "allocation_events.company_id"
                ),
                (
                    "purchase_value_correction_"
                    "allocation_events.id"
                ),
                (
                    "purchase_value_correction_"
                    "allocation_events."
                    "trade_value_correction_event_id"
                ),
                (
                    "purchase_value_correction_"
                    "allocation_events."
                    "invoice_fulfillment_allocation_id"
                ),
            ],
            name="fk_pvca_event_reversal_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pvca_event_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            "trade_value_correction_event_id",
            "invoice_fulfillment_allocation_id",
            name="uq_pvca_event_company_id_id_source",
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_pvca_event_reversal_of",
        ),
    )

    op.create_index(
        "ix_pvca_event_source",
        "purchase_value_correction_allocation_events",
        [
            "company_id",
            "trade_value_correction_event_id",
            "invoice_fulfillment_allocation_id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pvca_event_source",
        table_name=(
            "purchase_value_correction_allocation_events"
        ),
    )

    op.drop_table(
        "purchase_value_correction_allocation_events"
    )
