"""add opening balance provenance

Revision ID: af229fa18ec8
Revises: 230ce697c07a
"""

from itertools import combinations

from alembic import op
import sqlalchemy as sa


revision = "af229fa18ec8"
down_revision = "230ce697c07a"
branch_labels = None
depends_on = None


BUSINESS_SOURCES = [
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
    "purchase_value_correction_vat_adjustment_event_id",
    "purchase_value_correction_input_vat_credit_correction_event_id",
]


def _business_source_exclusivity(columns):
    return " AND ".join(
        f"({left} IS NULL OR {right} IS NULL)"
        for left, right in combinations(columns, 2)
    )


def _rk_exclusivity(columns):
    joined = ", ".join(columns)

    return (
        "tax_invoice_correction_line_id IS NULL "
        f"OR num_nonnulls({joined}) = 0"
    )


def upgrade():
    op.create_table(
        "opening_balances",
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
            "opening_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "journal_entry_id",
            sa.Integer(),
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
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"],
            ["journal_entries.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_opening_balances_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "journal_entry_id",
            name="uq_opening_balances_company_journal",
        ),
        sa.UniqueConstraint(
            "journal_entry_id",
            name="uq_opening_balances_journal_entry_id",
        ),
    )

    op.create_index(
        "ix_opening_balances_company_id",
        "opening_balances",
        ["company_id"],
        unique=False,
    )

    op.create_index(
        "ix_opening_balances_opening_date",
        "opening_balances",
        ["opening_date"],
        unique=False,
    )

    op.add_column(
        "journal_entries",
        sa.Column(
            "opening_balance_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_journal_entries_opening_balance_id_opening_balances",
        "journal_entries",
        "opening_balances",
        ["opening_balance_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_je_company_opening_balance",
        "journal_entries",
        "opening_balances",
        ["company_id", "opening_balance_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_index(
        "ix_je_opening_balance_id",
        "journal_entries",
        ["opening_balance_id"],
        unique=False,
    )

    op.create_index(
        "uq_je_original_opening_balance",
        "journal_entries",
        ["opening_balance_id"],
        unique=True,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL "
            "AND opening_balance_id IS NOT NULL"
        ),
    )

    op.drop_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        _business_source_exclusivity(
            BUSINESS_SOURCES + ["opening_balance_id"]
        ),
    )

    op.drop_constraint(
        "ck_je_rk_exclusive",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_je_rk_exclusive",
        "journal_entries",
        _rk_exclusivity(
            BUSINESS_SOURCES + ["opening_balance_id"]
        ),
    )


def downgrade():
    op.drop_constraint(
        "ck_je_rk_exclusive",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_je_rk_exclusive",
        "journal_entries",
        _rk_exclusivity(BUSINESS_SOURCES),
    )

    op.drop_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        _business_source_exclusivity(
            BUSINESS_SOURCES
        ),
    )

    op.drop_index(
        "uq_je_original_opening_balance",
        table_name="journal_entries",
    )

    op.drop_index(
        "ix_je_opening_balance_id",
        table_name="journal_entries",
    )

    op.drop_constraint(
        "fk_je_company_opening_balance",
        "journal_entries",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_journal_entries_opening_balance_id_opening_balances",
        "journal_entries",
        type_="foreignkey",
    )

    op.drop_column(
        "journal_entries",
        "opening_balance_id",
    )

    op.drop_index(
        "ix_opening_balances_opening_date",
        table_name="opening_balances",
    )

    op.drop_index(
        "ix_opening_balances_company_id",
        table_name="opening_balances",
    )

    op.drop_table("opening_balances")
