"""add input vat fulfillment bridge event

Revision ID: badc72a56036
Revises: 2dd0eaff8351
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'badc72a56036'
down_revision: Union[str, Sequence[str], None] = "2dd0eaff8351"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "input_vat_fulfillment_bridge_events",
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
            "tax_calculation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "invoice_fulfillment_allocation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "bridge_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "bridged_tax_amount",
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
            "bridged_tax_amount > 0",
            name=(
                'ck_ivfb_event_tax_amount_positive'
            ),
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                'ck_ivfb_event_currency_len'
            ),
        ),
        sa.CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                'ck_ivfb_event_not_self_reversal'
            ),
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
                'fk_ivfb_event_company_allocation'
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "tax_calculation_id",
                "invoice_fulfillment_allocation_id",
            ],
            [
                (
                    "input_vat_fulfillment_bridge_events."
                    "company_id"
                ),
                (
                    "input_vat_fulfillment_bridge_events."
                    "id"
                ),
                (
                    "input_vat_fulfillment_bridge_events."
                    "tax_calculation_id"
                ),
                (
                    "input_vat_fulfillment_bridge_events."
                    "invoice_fulfillment_allocation_id"
                ),
            ],
            name=(
                'fk_ivfb_event_reversal_source'
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "tax_calculation_id",
            ],
            [
                "tax_calculations.company_id",
                "tax_calculations.id",
            ],
            name=(
                'fk_ivfb_event_company_tax_calc'
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
        sa.ForeignKeyConstraint(
            [
                "created_by",
            ],
            [
                "users.id",
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
                'uq_ivfb_event_company_id_id'
            ),
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            "tax_calculation_id",
            "invoice_fulfillment_allocation_id",
            name=(
                'uq_ivfb_event_company_id_id_source'
            ),
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name=(
                'uq_ivfb_event_reversal_of'
            ),
        ),
    )

    op.create_index(
        op.f(
            'ix_ivfb_event_bridge_date'
        ),
        "input_vat_fulfillment_bridge_events",
        [
            "bridge_date",
        ],
        unique=False,
    )

    op.create_index(
        op.f(
            'ix_ivfb_event_company'
        ),
        "input_vat_fulfillment_bridge_events",
        [
            "company_id",
        ],
        unique=False,
    )

    op.create_index(
        op.f(
            'ix_ivfb_event_allocation'
        ),
        "input_vat_fulfillment_bridge_events",
        [
            "invoice_fulfillment_allocation_id",
        ],
        unique=False,
    )

    op.create_index(
        op.f(
            'ix_ivfb_event_tax_calc'
        ),
        "input_vat_fulfillment_bridge_events",
        [
            "tax_calculation_id",
        ],
        unique=False,
    )

    op.create_index(
        (
            'ix_ivfb_event_source_original'
        ),
        "input_vat_fulfillment_bridge_events",
        [
            "company_id",
            "tax_calculation_id",
            "invoice_fulfillment_allocation_id",
        ],
        unique=False,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        (
            'ix_ivfb_event_source_original'
        ),
        table_name=(
            "input_vat_fulfillment_bridge_events"
        ),
        postgresql_where=sa.text(
            "reversal_of_id IS NULL"
        ),
    )

    op.drop_index(
        op.f(
            'ix_ivfb_event_tax_calc'
        ),
        table_name=(
            "input_vat_fulfillment_bridge_events"
        ),
    )

    op.drop_index(
        op.f(
            'ix_ivfb_event_allocation'
        ),
        table_name=(
            "input_vat_fulfillment_bridge_events"
        ),
    )

    op.drop_index(
        op.f(
            'ix_ivfb_event_company'
        ),
        table_name=(
            "input_vat_fulfillment_bridge_events"
        ),
    )

    op.drop_index(
        op.f(
            'ix_ivfb_event_bridge_date'
        ),
        table_name=(
            "input_vat_fulfillment_bridge_events"
        ),
    )

    op.drop_table(
        "input_vat_fulfillment_bridge_events"
    )
