"""add supplier advance clearing event

Revision ID: d6b8a4c2e913
Revises: c1f3a8d42e71
"""

from alembic import op
import sqlalchemy as sa


revision = "d6b8a4c2e913"
down_revision = "c1f3a8d42e71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_advance_clearing_events",
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
            "payment_settlement_allocation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "invoice_fulfillment_allocation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "clearing_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "cleared_amount",
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
            "cleared_amount > 0",
            name="ck_sac_event_amount_positive",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_sac_event_currency_len",
        ),
        sa.CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_sac_event_"
                "not_self_reversal"
            ),
        ),
        sa.ForeignKeyConstraint(
            (
                "company_id",
                "payment_settlement_allocation_id",
            ),
            (
                "payment_settlement_allocations.company_id",
                "payment_settlement_allocations.id",
            ),
            name=(
                "fk_sac_event_"
                "company_settlement"
            ),
        ),
        sa.ForeignKeyConstraint(
            (
                "company_id",
                "invoice_fulfillment_allocation_id",
            ),
            (
                "invoice_fulfillment_allocations.company_id",
                "invoice_fulfillment_allocations.id",
            ),
            name=(
                "fk_sac_event_"
                "company_liability"
            ),
        ),
        sa.ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "payment_settlement_allocation_id",
                "invoice_fulfillment_allocation_id",
            ),
            (
                "supplier_advance_clearing_events.company_id",
                "supplier_advance_clearing_events.id",
                (
                    "supplier_advance_clearing_events."
                    "payment_settlement_allocation_id"
                ),
                (
                    "supplier_advance_clearing_events."
                    "invoice_fulfillment_allocation_id"
                ),
            ),
            name=(
                "fk_sac_event_"
                "reversal_source"
            ),
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_sac_event_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            "payment_settlement_allocation_id",
            "invoice_fulfillment_allocation_id",
            name=(
                "uq_sac_event_"
                "company_id_id_source"
            ),
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_sac_event_reversal_of",
        ),
    )

    op.create_index(
        "ix_sac_event_company",
        "supplier_advance_clearing_events",
        [
            "company_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_sac_event_settlement",
        "supplier_advance_clearing_events",
        [
            "payment_settlement_allocation_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_sac_event_liability",
        "supplier_advance_clearing_events",
        [
            "invoice_fulfillment_allocation_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_sac_event_date",
        "supplier_advance_clearing_events",
        [
            "clearing_date",
        ],
        unique=False,
    )

    op.create_index(
        "ix_sac_event_source_original",
        "supplier_advance_clearing_events",
        [
            "company_id",
            "payment_settlement_allocation_id",
            "invoice_fulfillment_allocation_id",
            "id",
        ],
        unique=False,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sac_event_source_original",
        table_name=(
            "supplier_advance_clearing_events"
        ),
    )

    op.drop_index(
        "ix_sac_event_date",
        table_name=(
            "supplier_advance_clearing_events"
        ),
    )

    op.drop_index(
        "ix_sac_event_liability",
        table_name=(
            "supplier_advance_clearing_events"
        ),
    )

    op.drop_index(
        "ix_sac_event_settlement",
        table_name=(
            "supplier_advance_clearing_events"
        ),
    )

    op.drop_index(
        "ix_sac_event_company",
        table_name=(
            "supplier_advance_clearing_events"
        ),
    )

    op.drop_table(
        "supplier_advance_clearing_events"
    )
