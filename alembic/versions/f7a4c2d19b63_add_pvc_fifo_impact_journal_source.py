"""add PVC FIFO impact journal source

Revision ID: f7a4c2d19b63
Revises: e48e53066ae3
"""

from alembic import op
import sqlalchemy as sa


revision = "f7a4c2d19b63"
down_revision = "e48e53066ae3"
branch_labels = None
depends_on = None


SOURCE_EXCLUSIVITY_SQL = """
purchase_value_correction_fifo_impact_event_id IS NULL
OR (
    document_id IS NULL
    AND payment_id IS NULL
    AND payment_settlement_allocation_id IS NULL
    AND tax_recognition_event_id IS NULL
    AND sales_recognition_event_id IS NULL
    AND vat_advance_bridge_event_id IS NULL
    AND input_vat_fulfillment_bridge_event_id IS NULL
    AND supplier_advance_clearing_event_id IS NULL
    AND customer_advance_clearing_event_id IS NULL
    AND sales_return_recognition_event_id IS NULL
    AND sales_return_cost_restoration_event_id IS NULL
    AND purchase_return_recognition_event_id IS NULL
    AND purchase_return_vat_adjustment_event_id IS NULL
    AND purchase_return_input_vat_credit_correction_event_id IS NULL
)
"""


def upgrade() -> None:
    op.add_column(
        "journal_entries",
        sa.Column(
            "purchase_value_correction_fifo_impact_event_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_je_company_pvc_fifo_impact",
        "journal_entries",
        "purchase_value_correction_fifo_impact_events",
        [
            "company_id",
            "purchase_value_correction_fifo_impact_event_id",
        ],
        [
            "company_id",
            "id",
        ],
        ondelete="RESTRICT",
    )

    op.create_index(
        "ix_je_pvc_fifo_impact_event_id",
        "journal_entries",
        [
            "purchase_value_correction_fifo_impact_event_id",
        ],
        unique=False,
    )

    op.create_index(
        "uq_je_original_pvc_fifo_impact",
        "journal_entries",
        [
            "purchase_value_correction_fifo_impact_event_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL "
            "AND purchase_value_correction_fifo_impact_event_id "
            "IS NOT NULL"
        ),
    )

    op.create_check_constraint(
        "ck_je_pvc_fifo_impact_source_exclusive",
        "journal_entries",
        SOURCE_EXCLUSIVITY_SQL,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_je_pvc_fifo_impact_source_exclusive",
        "journal_entries",
        type_="check",
    )

    op.drop_index(
        "uq_je_original_pvc_fifo_impact",
        table_name="journal_entries",
    )

    op.drop_index(
        "ix_je_pvc_fifo_impact_event_id",
        table_name="journal_entries",
    )

    op.drop_constraint(
        "fk_je_company_pvc_fifo_impact",
        "journal_entries",
        type_="foreignkey",
    )

    op.drop_column(
        "journal_entries",
        "purchase_value_correction_fifo_impact_event_id",
    )
