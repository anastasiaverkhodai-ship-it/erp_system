"""add customer advance clearing event

Revision ID: f4c2a9e7d118
Revises: e7c9f1a5b204
"""

from alembic import op
import sqlalchemy as sa


revision = "f4c2a9e7d118"
down_revision = "e7c9f1a5b204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_advance_clearing_events",
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
            "sales_recognition_event_id",
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
            name="ck_cac_event_amount_positive",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_cac_event_currency_len",
        ),
        sa.CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_cac_event_"
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
                "fk_cac_event_"
                "company_settlement"
            ),
        ),
        sa.ForeignKeyConstraint(
            (
                "company_id",
                "sales_recognition_event_id",
            ),
            (
                "sales_recognition_events.company_id",
                "sales_recognition_events.id",
            ),
            name=(
                "fk_cac_event_"
                "company_liability"
            ),
        ),
        sa.ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "payment_settlement_allocation_id",
                "sales_recognition_event_id",
            ),
            (
                "customer_advance_clearing_events.company_id",
                "customer_advance_clearing_events.id",
                (
                    "customer_advance_clearing_events."
                    "payment_settlement_allocation_id"
                ),
                (
                    "customer_advance_clearing_events."
                    "sales_recognition_event_id"
                ),
            ),
            name=(
                "fk_cac_event_"
                "reversal_source"
            ),
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_cac_event_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            "payment_settlement_allocation_id",
            "sales_recognition_event_id",
            name=(
                "uq_cac_event_"
                "company_id_id_source"
            ),
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_cac_event_reversal_of",
        ),
    )

    op.create_index(
        "ix_cac_event_company",
        "customer_advance_clearing_events",
        [
            "company_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_cac_event_settlement",
        "customer_advance_clearing_events",
        [
            "payment_settlement_allocation_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_cac_event_liability",
        "customer_advance_clearing_events",
        [
            "sales_recognition_event_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_cac_event_date",
        "customer_advance_clearing_events",
        [
            "clearing_date",
        ],
        unique=False,
    )

    op.create_index(
        "ix_cac_event_source_original",
        "customer_advance_clearing_events",
        [
            "company_id",
            "payment_settlement_allocation_id",
            "sales_recognition_event_id",
            "id",
        ],
        unique=False,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cac_event_source_original",
        table_name=(
            "customer_advance_clearing_events"
        ),
    )

    op.drop_index(
        "ix_cac_event_date",
        table_name=(
            "customer_advance_clearing_events"
        ),
    )

    op.drop_index(
        "ix_cac_event_liability",
        table_name=(
            "customer_advance_clearing_events"
        ),
    )

    op.drop_index(
        "ix_cac_event_settlement",
        table_name=(
            "customer_advance_clearing_events"
        ),
    )

    op.drop_index(
        "ix_cac_event_company",
        table_name=(
            "customer_advance_clearing_events"
        ),
    )

    op.drop_table(
        "customer_advance_clearing_events"
    )
