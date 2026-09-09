"""add purchase value correction fifo impact events

Revision ID: e48e53066ae3
Revises: c0f1a7e9d432
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e48e53066ae3"
down_revision: str | None = "c0f1a7e9d432"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchase_value_correction_fifo_impact_events",
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
            "purchase_value_correction_allocation_event_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "stock_lot_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "destination_kind",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "stock_lot_consumption_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "issue_document_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "issue_document_line_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "recognition_date",
            sa.Date(),
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
            "original_base_amount",
            sa.Numeric(
                precision=18,
                scale=2,
            ),
            nullable=False,
        ),
        sa.Column(
            "corrected_base_amount",
            sa.Numeric(
                precision=18,
                scale=2,
            ),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(length=3),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "reversal_of_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.CheckConstraint(
            (
                "destination_kind IN "
                "('issued', 'on_hand')"
            ),
            name="ck_pvcfi_event_destination_kind",
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_pvcfi_event_quantity_positive",
        ),
        sa.CheckConstraint(
            "original_base_amount >= 0",
            name="ck_pvcfi_event_original_nonnegative",
        ),
        sa.CheckConstraint(
            "corrected_base_amount >= 0",
            name="ck_pvcfi_event_corrected_nonnegative",
        ),
        sa.CheckConstraint(
            (
                "original_base_amount "
                "<> corrected_base_amount"
            ),
            name="ck_pvcfi_event_not_noop",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_pvcfi_event_currency_length",
        ),
        sa.CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_pvcfi_event_not_self_reversal",
        ),
        sa.CheckConstraint(
            (
                "("
                "destination_kind = 'on_hand' "
                "AND stock_lot_consumption_id IS NULL "
                "AND issue_document_id IS NULL "
                "AND issue_document_line_id IS NULL"
                ") OR ("
                "destination_kind = 'issued' "
                "AND stock_lot_consumption_id IS NOT NULL "
                "AND issue_document_id IS NOT NULL "
                "AND issue_document_line_id IS NOT NULL"
                ")"
            ),
            name="ck_pvcfi_event_destination_provenance",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_pvcfi_event_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "purchase_value_correction_allocation_event_id",
            ],
            [
                "purchase_value_correction_allocation_events.company_id",
                "purchase_value_correction_allocation_events.id",
            ],
            name="fk_pvcfi_event_allocation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stock_lot_id"],
            ["stock_lots.id"],
            name="fk_pvcfi_event_stock_lot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stock_lot_consumption_id"],
            ["stock_lot_consumptions.id"],
            name="fk_pvcfi_event_consumption",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["issue_document_id"],
            ["documents.id"],
            name="fk_pvcfi_event_issue_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["issue_document_line_id"],
            ["document_lines.id"],
            name="fk_pvcfi_event_issue_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_pvcfi_event_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_of_id"],
            [
                "purchase_value_correction_fifo_impact_events.id"
            ],
            name="fk_pvcfi_event_reversal",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=(
                "pk_purchase_value_correction_"
                "fifo_impact_events"
            ),
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pvcfi_event_company_id_id",
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_pvcfi_event_reversal_of",
        ),
    )

    op.create_index(
        "ix_pvcfi_event_allocation",
        "purchase_value_correction_fifo_impact_events",
        [
            "company_id",
            "purchase_value_correction_allocation_event_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_pvcfi_event_stock_lot",
        "purchase_value_correction_fifo_impact_events",
        [
            "company_id",
            "stock_lot_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_pvcfi_event_consumption",
        "purchase_value_correction_fifo_impact_events",
        [
            "company_id",
            "stock_lot_consumption_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_pvcfi_event_recognition_date",
        "purchase_value_correction_fifo_impact_events",
        [
            "company_id",
            "recognition_date",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pvcfi_event_recognition_date",
        table_name=(
            "purchase_value_correction_fifo_impact_events"
        ),
    )
    op.drop_index(
        "ix_pvcfi_event_consumption",
        table_name=(
            "purchase_value_correction_fifo_impact_events"
        ),
    )
    op.drop_index(
        "ix_pvcfi_event_stock_lot",
        table_name=(
            "purchase_value_correction_fifo_impact_events"
        ),
    )
    op.drop_index(
        "ix_pvcfi_event_allocation",
        table_name=(
            "purchase_value_correction_fifo_impact_events"
        ),
    )
    op.drop_table(
        "purchase_value_correction_fifo_impact_events"
    )
