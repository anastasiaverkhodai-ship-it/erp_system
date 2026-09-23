"""Year-end lifecycle and journal provenance.

Revision ID: c0441bc30ae0
Revises: bf330ab29fd9
"""
from alembic import op
import sqlalchemy as sa

revision = "c0441bc30ae0"
down_revision = "bf330ab29fd9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("year_end_closings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_key", sa.String(255), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("preview_fingerprint", sa.String(64), nullable=False),
        sa.Column("journal_entry_id", sa.Integer(), nullable=True, unique=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reversed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("company_id", "id", name="uq_year_end_company_id"),
        sa.UniqueConstraint("company_id", "request_key", name="uq_year_end_request"),
        sa.CheckConstraint("year BETWEEN 2000 AND 2100", name="ck_year_end_year"),
        sa.CheckConstraint("status IN ('closed', 'reversed')", name="ck_year_end_status"),
        sa.ForeignKeyConstraint(["company_id", "journal_entry_id"], ["journal_entries.company_id", "journal_entries.id"], name="fk_year_end_company_journal", ondelete="RESTRICT"),
    )
    op.create_index("ix_year_end_closings_company_id", "year_end_closings", ["company_id"])
    op.create_index("uq_year_end_active", "year_end_closings", ["company_id", "year"], unique=True, postgresql_where=sa.text("status = 'closed'"))
    op.add_column("journal_entries", sa.Column("year_end_closing_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_je_company_year_end", "journal_entries", "year_end_closings", ["company_id", "year_end_closing_id"], ["company_id", "id"], ondelete="RESTRICT")
    op.create_index("ix_je_year_end_closing", "journal_entries", ["year_end_closing_id"])
    op.create_index("uq_je_year_end_original", "journal_entries", ["year_end_closing_id"], unique=True, postgresql_where=sa.text("year_end_closing_id IS NOT NULL AND reversal_of_id IS NULL"))
    op.create_check_constraint("ck_je_year_end_exclusive", "journal_entries", 'year_end_closing_id IS NULL OR num_nonnulls(document_id,payment_id,payment_settlement_allocation_id,tax_recognition_event_id,sales_recognition_event_id,vat_advance_bridge_event_id,input_vat_fulfillment_bridge_event_id,supplier_advance_clearing_event_id,customer_advance_clearing_event_id,sales_return_recognition_event_id,purchase_value_correction_fifo_impact_event_id,purchase_value_correction_ma_replay_event_id,sales_return_cost_restoration_event_id,purchase_return_recognition_event_id,purchase_return_vat_adjustment_event_id,purchase_return_input_vat_credit_correction_event_id,purchase_value_correction_vat_adjustment_event_id,purchase_value_correction_input_vat_credit_correction_event_id,opening_balance_id,tax_invoice_correction_line_id) = 0')


def downgrade():
    op.drop_constraint("ck_je_year_end_exclusive", "journal_entries", type_="check")
    op.drop_index("uq_je_year_end_original", table_name="journal_entries")
    op.drop_index("ix_je_year_end_closing", table_name="journal_entries")
    op.drop_constraint("fk_je_company_year_end", "journal_entries", type_="foreignkey")
    op.drop_column("journal_entries", "year_end_closing_id")
    op.drop_index("uq_year_end_active", table_name="year_end_closings")
    op.drop_index("ix_year_end_closings_company_id", table_name="year_end_closings")
    op.drop_table("year_end_closings")
