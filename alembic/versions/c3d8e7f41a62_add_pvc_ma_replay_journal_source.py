"""add PVC moving-average replay journal source

Revision ID: c3d8e7f41a62
Revises: a91c4f2e7d10

"""

from alembic import op
import sqlalchemy as sa


revision = "c3d8e7f41a62"
down_revision = "a91c4f2e7d10"
branch_labels = None
depends_on = None


GLOBAL_SOURCE_PAIRS = (
    "document_id",
    "payment_id",
    "payment_settlement_allocation_id",
    "tax_recognition_event_id",
    "sales_recognition_event_id",
    "vat_advance_bridge_event_id",
    "input_vat_fulfillment_bridge_event_id",
    "supplier_advance_clearing_event_id",
    "customer_advance_clearing_event_id",
    "sales_return_recognition_event_id",
    "sales_return_cost_restoration_event_id",
    "purchase_return_recognition_event_id",
    "purchase_return_vat_adjustment_event_id",
    "purchase_return_input_vat_credit_correction_event_id",
    "purchase_value_correction_fifo_impact_event_id",
)


MA_SOURCE_EXCLUSIVITY_SQL = """
purchase_value_correction_ma_replay_event_id IS NULL
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
    AND purchase_value_correction_fifo_impact_event_id IS NULL
)
"""


# Exact pre-MA global constraint from the current JournalEntry model.
OLD_GLOBAL_SOURCE_EXCLUSIVITY_SQL = """
(
    document_id IS NULL
    OR payment_id IS NULL
)
AND
(
    document_id IS NULL
    OR payment_settlement_allocation_id IS NULL
)
AND
(
    document_id IS NULL
    OR tax_recognition_event_id IS NULL
)
AND
(
    document_id IS NULL
    OR sales_recognition_event_id IS NULL
)
AND
(
    document_id IS NULL
    OR vat_advance_bridge_event_id IS NULL
)
AND
(
    document_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
AND
(
    document_id IS NULL
    OR supplier_advance_clearing_event_id IS NULL
)
AND
(
    document_id IS NULL
    OR customer_advance_clearing_event_id IS NULL
)
AND
(
    document_id IS NULL
    OR sales_return_recognition_event_id IS NULL
)
AND
(
    document_id IS NULL
    OR sales_return_cost_restoration_event_id IS NULL
)
AND
(
    document_id IS NULL
    OR purchase_return_recognition_event_id IS NULL
)
AND
(
    document_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    document_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR payment_settlement_allocation_id IS NULL
)
AND
(
    payment_id IS NULL
    OR tax_recognition_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR sales_recognition_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR vat_advance_bridge_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR supplier_advance_clearing_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR customer_advance_clearing_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR sales_return_recognition_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR sales_return_cost_restoration_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR purchase_return_recognition_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR tax_recognition_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR sales_recognition_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR vat_advance_bridge_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR supplier_advance_clearing_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR customer_advance_clearing_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR sales_return_recognition_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR sales_return_cost_restoration_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR purchase_return_recognition_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR sales_recognition_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR vat_advance_bridge_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR supplier_advance_clearing_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR customer_advance_clearing_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR sales_return_recognition_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR sales_return_cost_restoration_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR purchase_return_recognition_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    sales_recognition_event_id IS NULL
    OR vat_advance_bridge_event_id IS NULL
)
AND
(
    sales_recognition_event_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
AND
(
    sales_recognition_event_id IS NULL
    OR supplier_advance_clearing_event_id IS NULL
)
AND
(
    sales_recognition_event_id IS NULL
    OR customer_advance_clearing_event_id IS NULL
)
AND
(
    sales_recognition_event_id IS NULL
    OR sales_return_recognition_event_id IS NULL
)
AND
(
    sales_recognition_event_id IS NULL
    OR sales_return_cost_restoration_event_id IS NULL
)
AND
(
    sales_recognition_event_id IS NULL
    OR purchase_return_recognition_event_id IS NULL
)
AND
(
    sales_recognition_event_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    sales_recognition_event_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    vat_advance_bridge_event_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
AND
(
    vat_advance_bridge_event_id IS NULL
    OR supplier_advance_clearing_event_id IS NULL
)
AND
(
    vat_advance_bridge_event_id IS NULL
    OR customer_advance_clearing_event_id IS NULL
)
AND
(
    vat_advance_bridge_event_id IS NULL
    OR sales_return_recognition_event_id IS NULL
)
AND
(
    vat_advance_bridge_event_id IS NULL
    OR sales_return_cost_restoration_event_id IS NULL
)
AND
(
    vat_advance_bridge_event_id IS NULL
    OR purchase_return_recognition_event_id IS NULL
)
AND
(
    vat_advance_bridge_event_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    vat_advance_bridge_event_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    input_vat_fulfillment_bridge_event_id IS NULL
    OR supplier_advance_clearing_event_id IS NULL
)
AND
(
    input_vat_fulfillment_bridge_event_id IS NULL
    OR customer_advance_clearing_event_id IS NULL
)
AND
(
    input_vat_fulfillment_bridge_event_id IS NULL
    OR sales_return_recognition_event_id IS NULL
)
AND
(
    input_vat_fulfillment_bridge_event_id IS NULL
    OR sales_return_cost_restoration_event_id IS NULL
)
AND
(
    input_vat_fulfillment_bridge_event_id IS NULL
    OR purchase_return_recognition_event_id IS NULL
)
AND
(
    input_vat_fulfillment_bridge_event_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    input_vat_fulfillment_bridge_event_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    supplier_advance_clearing_event_id IS NULL
    OR customer_advance_clearing_event_id IS NULL
)
AND
(
    supplier_advance_clearing_event_id IS NULL
    OR sales_return_recognition_event_id IS NULL
)
AND
(
    supplier_advance_clearing_event_id IS NULL
    OR sales_return_cost_restoration_event_id IS NULL
)
AND
(
    supplier_advance_clearing_event_id IS NULL
    OR purchase_return_recognition_event_id IS NULL
)
AND
(
    supplier_advance_clearing_event_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    supplier_advance_clearing_event_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    customer_advance_clearing_event_id IS NULL
    OR sales_return_recognition_event_id IS NULL
)
AND
(
    customer_advance_clearing_event_id IS NULL
    OR sales_return_cost_restoration_event_id IS NULL
)
AND
(
    customer_advance_clearing_event_id IS NULL
    OR purchase_return_recognition_event_id IS NULL
)
AND
(
    customer_advance_clearing_event_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    customer_advance_clearing_event_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    sales_return_recognition_event_id IS NULL
    OR sales_return_cost_restoration_event_id IS NULL
)
AND
(
    sales_return_recognition_event_id IS NULL
    OR purchase_return_recognition_event_id IS NULL
)
AND
(
    sales_return_recognition_event_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    sales_return_recognition_event_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    sales_return_cost_restoration_event_id IS NULL
    OR purchase_return_recognition_event_id IS NULL
)
AND
(
    sales_return_cost_restoration_event_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    sales_return_cost_restoration_event_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    purchase_return_recognition_event_id IS NULL
    OR purchase_return_vat_adjustment_event_id IS NULL
)
AND
(
    purchase_return_recognition_event_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
AND
(
    purchase_return_vat_adjustment_event_id IS NULL
    OR purchase_return_input_vat_credit_correction_event_id IS NULL
)
"""


def _new_global_source_exclusivity_sql() -> str:
    additions = "\n".join(
        (
            "AND\n(\n"
            f"    {source} IS NULL\n"
            "    OR purchase_value_correction_ma_replay_event_id IS NULL\n"
            ")"
        )
        for source in GLOBAL_SOURCE_PAIRS
    )
    return (
        OLD_GLOBAL_SOURCE_EXCLUSIVITY_SQL.strip()
        + "\n"
        + additions
    )


def upgrade() -> None:
    op.add_column(
        "journal_entries",
        sa.Column(
            "purchase_value_correction_ma_replay_event_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_je_company_pvc_ma_replay",
        "journal_entries",
        "purchase_value_correction_ma_replay_events",
        [
            "company_id",
            "purchase_value_correction_ma_replay_event_id",
        ],
        [
            "company_id",
            "id",
        ],
        ondelete="RESTRICT",
    )

    op.create_index(
        "ix_je_pvc_ma_replay_event_id",
        "journal_entries",
        [
            "purchase_value_correction_ma_replay_event_id",
        ],
        unique=False,
    )

    op.create_index(
        "uq_je_original_pvc_ma_replay",
        "journal_entries",
        [
            "purchase_value_correction_ma_replay_event_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL "
            "AND purchase_value_correction_ma_replay_event_id "
            "IS NOT NULL"
        ),
    )

    op.create_check_constraint(
        "ck_je_pvc_ma_replay_source_exclusive",
        "journal_entries",
        MA_SOURCE_EXCLUSIVITY_SQL,
    )

    op.drop_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        _new_global_source_exclusivity_sql(),
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        OLD_GLOBAL_SOURCE_EXCLUSIVITY_SQL,
    )

    op.drop_constraint(
        "ck_je_pvc_ma_replay_source_exclusive",
        "journal_entries",
        type_="check",
    )

    op.drop_index(
        "uq_je_original_pvc_ma_replay",
        table_name="journal_entries",
    )

    op.drop_index(
        "ix_je_pvc_ma_replay_event_id",
        table_name="journal_entries",
    )

    op.drop_constraint(
        "fk_je_company_pvc_ma_replay",
        "journal_entries",
        type_="foreignkey",
    )

    op.drop_column(
        "journal_entries",
        "purchase_value_correction_ma_replay_event_id",
    )
