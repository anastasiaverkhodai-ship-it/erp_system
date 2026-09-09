"""add PVC VAT and RK lifecycle

Revision ID: d91f6a2c4e70
Revises: c3d8e7f41a62
"""

from itertools import combinations

from alembic import op
import sqlalchemy as sa


revision = "d91f6a2c4e70"
down_revision = "c3d8e7f41a62"
branch_labels = None
depends_on = None


BUSINESS_SOURCES_BEFORE = (
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
    "purchase_value_correction_fifo_impact_event_id",
    "purchase_value_correction_ma_replay_event_id",
    "sales_return_cost_restoration_event_id",
    "purchase_return_recognition_event_id",
    "purchase_return_vat_adjustment_event_id",
    "purchase_return_input_vat_credit_correction_event_id",
)

BUSINESS_SOURCES_AFTER = (
    *BUSINESS_SOURCES_BEFORE,
    "purchase_value_correction_vat_adjustment_event_id",
    (
        "purchase_value_correction_input_vat_"
        "credit_correction_event_id"
    ),
)


def _source_check(
    columns: tuple[str, ...],
) -> sa.TextClause:
    return sa.text(
        "\nAND\n".join(
            (
                f"({left} IS NULL "
                f"OR {right} IS NULL)"
            )
            for left, right in combinations(
                columns,
                2,
            )
        )
    )


def upgrade() -> None:
    # =====================================================
    # 1. Economic PVC VAT adjustment event
    # =====================================================

    op.create_table(
        "purchase_value_correction_vat_adjustment_events",
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
            "tax_calculation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "adjustment_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "adjustment_kind",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "adjusted_taxable_base",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "adjusted_tax_amount",
            sa.Numeric(18, 2),
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
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "reversal_of_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.PrimaryKeyConstraint(
            "id",
        ),

        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pvcvae_event_company_id_id",
        ),

        sa.UniqueConstraint(
            "company_id",
            "id",
            "trade_value_correction_event_id",
            "tax_calculation_id",
            "adjustment_kind",
            name="uq_pvcvae_event_company_id_id_source",
        ),

        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_pvcvae_event_reversal_of",
        ),

        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_pvcvae_event_company",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_pvcvae_event_created_by",
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
                "fk_pvcvae_event_company_"
                "trade_value_correction"
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
                "fk_pvcvae_event_company_"
                "tax_calculation"
            ),
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "trade_value_correction_event_id",
                "tax_calculation_id",
                "adjustment_kind",
            ],
            [
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events.company_id"
                ),
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events.id"
                ),
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events."
                    "trade_value_correction_event_id"
                ),
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events.tax_calculation_id"
                ),
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events.adjustment_kind"
                ),
            ],
            name="fk_pvcvae_event_reversal_source",
            ondelete="RESTRICT",
        ),

        sa.CheckConstraint(
            "adjustment_kind IN ('decrease', 'increase')",
            name="ck_pvcvae_event_adjustment_kind",
        ),

        sa.CheckConstraint(
            "adjusted_taxable_base >= 0",
            name="ck_pvcvae_event_base_nonnegative",
        ),

        sa.CheckConstraint(
            "adjusted_tax_amount >= 0",
            name="ck_pvcvae_event_tax_nonnegative",
        ),

        sa.CheckConstraint(
            (
                "adjusted_taxable_base > 0 "
                "OR adjusted_tax_amount > 0"
            ),
            name="ck_pvcvae_event_nonzero",
        ),

        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_pvcvae_event_currency_length",
        ),

        sa.CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_pvcvae_event_not_self_reversal",
        ),
    )

    op.create_index(
        "ix_pvcvae_event_source_history",
        "purchase_value_correction_vat_adjustment_events",
        [
            "company_id",
            "trade_value_correction_event_id",
            "tax_calculation_id",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_pvcvae_event_tax_calculation",
        "purchase_value_correction_vat_adjustment_events",
        [
            "company_id",
            "tax_calculation_id",
            "adjustment_date",
            "id",
        ],
        unique=False,
    )

    # =====================================================
    # 2. Legal PVC INPUT VAT credit correction event
    # =====================================================

    legal_table = (
        "purchase_value_correction_input_vat_"
        "credit_correction_events"
    )

    op.create_table(
        legal_table,

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
            "purchase_value_correction_vat_adjustment_event_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "tax_calculation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "tax_credit_evidence_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "adjustment_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "correction_kind",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "corrected_taxable_base",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "corrected_tax_amount",
            sa.Numeric(18, 2),
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
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "reversal_of_id",
            sa.Integer(),
            nullable=True,
        ),

        sa.PrimaryKeyConstraint(
            "id",
        ),

        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pvcivcc_event_company_id_id",
        ),

        sa.UniqueConstraint(
            "company_id",
            "id",
            "purchase_value_correction_vat_adjustment_event_id",
            "tax_calculation_id",
            "correction_kind",
            name="uq_pvcivcc_event_company_id_id_source",
        ),

        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_pvcivcc_event_reversal_of",
        ),

        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_pvcivcc_event_company",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_pvcivcc_event_created_by",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            [
                "company_id",
                "purchase_value_correction_vat_adjustment_event_id",
            ],
            [
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events.company_id"
                ),
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events.id"
                ),
            ],
            name=(
                "fk_pvcivcc_event_company_"
                "pvc_vat_adjustment"
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
                "fk_pvcivcc_event_company_"
                "tax_calculation"
            ),
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            [
                "company_id",
                "tax_credit_evidence_id",
            ],
            [
                "tax_credit_evidence.company_id",
                "tax_credit_evidence.id",
            ],
            name=(
                "fk_pvcivcc_event_company_"
                "tax_credit_evidence"
            ),
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "purchase_value_correction_vat_adjustment_event_id",
                "tax_calculation_id",
                "correction_kind",
            ],
            [
                (
                    "purchase_value_correction_input_vat_"
                    "credit_correction_events.company_id"
                ),
                (
                    "purchase_value_correction_input_vat_"
                    "credit_correction_events.id"
                ),
                (
                    "purchase_value_correction_input_vat_"
                    "credit_correction_events."
                    "purchase_value_correction_vat_"
                    "adjustment_event_id"
                ),
                (
                    "purchase_value_correction_input_vat_"
                    "credit_correction_events."
                    "tax_calculation_id"
                ),
                (
                    "purchase_value_correction_input_vat_"
                    "credit_correction_events."
                    "correction_kind"
                ),
            ],
            name="fk_pvcivcc_event_reversal_source",
            ondelete="RESTRICT",
        ),

        sa.CheckConstraint(
            "correction_kind IN ('decrease', 'increase')",
            name="ck_pvcivcc_event_correction_kind",
        ),

        sa.CheckConstraint(
            "corrected_taxable_base >= 0",
            name="ck_pvcivcc_event_base_nonnegative",
        ),

        sa.CheckConstraint(
            "corrected_tax_amount >= 0",
            name="ck_pvcivcc_event_tax_nonnegative",
        ),

        sa.CheckConstraint(
            (
                "corrected_taxable_base > 0 "
                "OR corrected_tax_amount > 0"
            ),
            name="ck_pvcivcc_event_nonzero",
        ),

        sa.CheckConstraint(
            (
                "("
                "correction_kind = 'decrease' "
                "AND tax_credit_evidence_id IS NULL"
                ") OR ("
                "correction_kind = 'increase' "
                "AND tax_credit_evidence_id IS NOT NULL"
                ")"
            ),
            name="ck_pvcivcc_event_evidence_direction",
        ),

        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_pvcivcc_event_currency_length",
        ),

        sa.CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_pvcivcc_event_not_self_reversal",
        ),
    )

    op.create_index(
        "ix_pvcivcc_event_source_history",
        legal_table,
        [
            "company_id",
            "purchase_value_correction_vat_adjustment_event_id",
            "tax_calculation_id",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_pvcivcc_event_tax_calculation",
        legal_table,
        [
            "company_id",
            "tax_calculation_id",
            "adjustment_date",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_pvcivcc_event_tax_credit_evidence",
        legal_table,
        [
            "company_id",
            "tax_credit_evidence_id",
            "id",
        ],
        unique=False,
    )

    # =====================================================
    # 3. Typed JournalEntry sources
    # =====================================================

    op.add_column(
        "journal_entries",
        sa.Column(
            "purchase_value_correction_vat_adjustment_event_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "journal_entries",
        sa.Column(
            (
                "purchase_value_correction_input_vat_"
                "credit_correction_event_id"
            ),
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_je_company_pvc_vat_adjustment",
        "journal_entries",
        "purchase_value_correction_vat_adjustment_events",
        [
            "company_id",
            "purchase_value_correction_vat_adjustment_event_id",
        ],
        [
            "company_id",
            "id",
        ],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_je_company_pvc_input_vat_credit_correction",
        "journal_entries",
        legal_table,
        [
            "company_id",
            (
                "purchase_value_correction_input_vat_"
                "credit_correction_event_id"
            ),
        ],
        [
            "company_id",
            "id",
        ],
        ondelete="RESTRICT",
    )

    op.create_index(
        "ix_je_pvc_vat_adjustment_event_id",
        "journal_entries",
        [
            "purchase_value_correction_vat_adjustment_event_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_je_pvc_input_vat_credit_correction_event_id",
        "journal_entries",
        [
            (
                "purchase_value_correction_input_vat_"
                "credit_correction_event_id"
            ),
        ],
        unique=False,
    )

    op.create_index(
        "uq_je_original_pvc_vat_adjustment",
        "journal_entries",
        [
            "purchase_value_correction_vat_adjustment_event_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL "
            "AND "
            "purchase_value_correction_vat_adjustment_event_id "
            "IS NOT NULL"
        ),
    )

    op.create_index(
        "uq_je_original_pvc_input_vat_credit_correction",
        "journal_entries",
        [
            (
                "purchase_value_correction_input_vat_"
                "credit_correction_event_id"
            ),
        ],
        unique=True,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL "
            "AND "
            "purchase_value_correction_input_vat_"
            "credit_correction_event_id IS NOT NULL"
        ),
    )

    # Global exact-one-source contract:
    # 16 sources / 120 pairs -> 18 sources / 153 pairs.

    op.drop_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        _source_check(
            BUSINESS_SOURCES_AFTER
        ),
    )


def downgrade() -> None:
    legal_table = (
        "purchase_value_correction_input_vat_"
        "credit_correction_events"
    )

    op.drop_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        _source_check(
            BUSINESS_SOURCES_BEFORE
        ),
    )

    op.drop_index(
        "uq_je_original_pvc_input_vat_credit_correction",
        table_name="journal_entries",
    )

    op.drop_index(
        "uq_je_original_pvc_vat_adjustment",
        table_name="journal_entries",
    )

    op.drop_index(
        "ix_je_pvc_input_vat_credit_correction_event_id",
        table_name="journal_entries",
    )

    op.drop_index(
        "ix_je_pvc_vat_adjustment_event_id",
        table_name="journal_entries",
    )

    op.drop_constraint(
        "fk_je_company_pvc_input_vat_credit_correction",
        "journal_entries",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_je_company_pvc_vat_adjustment",
        "journal_entries",
        type_="foreignkey",
    )

    op.drop_column(
        "journal_entries",
        (
            "purchase_value_correction_input_vat_"
            "credit_correction_event_id"
        ),
    )

    op.drop_column(
        "journal_entries",
        "purchase_value_correction_vat_adjustment_event_id",
    )

    op.drop_index(
        "ix_pvcivcc_event_tax_credit_evidence",
        table_name=legal_table,
    )

    op.drop_index(
        "ix_pvcivcc_event_tax_calculation",
        table_name=legal_table,
    )

    op.drop_index(
        "ix_pvcivcc_event_source_history",
        table_name=legal_table,
    )

    op.drop_table(
        legal_table
    )

    op.drop_index(
        "ix_pvcvae_event_tax_calculation",
        table_name=(
            "purchase_value_correction_vat_adjustment_events"
        ),
    )

    op.drop_index(
        "ix_pvcvae_event_source_history",
        table_name=(
            "purchase_value_correction_vat_adjustment_events"
        ),
    )

    op.drop_table(
        "purchase_value_correction_vat_adjustment_events"
    )
