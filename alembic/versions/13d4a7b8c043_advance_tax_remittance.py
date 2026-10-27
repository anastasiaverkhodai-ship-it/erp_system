"""Advance tax remittance evidence.

Revision ID: 13d4a7b8c043
Revises: 13d4a7b8c042
"""

from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c043"
down_revision = "13d4a7b8c042"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_advance_tax_remittances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id", sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("payroll_advance_id", sa.Integer(), nullable=False),
        sa.Column("journal_entry_id", sa.Integer(), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), nullable=False),
        sa.Column("component", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("document_reference", sa.String(500), nullable=False),
        sa.Column("budget_payment_reference", sa.String(500), nullable=False),
        sa.Column(
            "created_by", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "company_id", "id",
            name="uq_advance_tax_remittance_company_id",
        ),
        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_advance_tax_remittance_request",
        ),
        sa.UniqueConstraint(
            "company_id", "journal_entry_id",
            name="uq_advance_tax_remittance_journal",
        ),
        sa.UniqueConstraint(
            "company_id", "payroll_advance_id", "component",
            name="uq_advance_tax_remittance_component",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "payroll_advance_id"],
            ["payroll_advances.company_id", "payroll_advances.id"],
            name="fk_advance_tax_remittance_advance",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "journal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_advance_tax_remittance_journal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "bank_account_id"],
            ["bank_accounts.company_id", "bank_accounts.id"],
            name="fk_advance_tax_remittance_bank",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "component IN ('personal_income_tax', 'military_levy')",
            name="ck_advance_tax_remittance_component",
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount <> 'NaN'::numeric",
            name="ck_advance_tax_remittance_positive",
        ),
    )


def downgrade():
    op.drop_table("payroll_advance_tax_remittances")
