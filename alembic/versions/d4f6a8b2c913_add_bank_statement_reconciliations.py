"""add bank statement reconciliations

Revision ID: d4f6a8b2c913
Revises: c8d2e5f4a701
"""

from alembic import op
import sqlalchemy as sa


revision = "d4f6a8b2c913"
down_revision = "c8d2e5f4a701"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bank_statement_reconciliations",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "bank_statement_line_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "payment_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "matched_amount",
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
            "status",
            sa.String(length=20),
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
            "reversed_by",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "reversed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=(
                "fk_bank_statement_reconciliations_company"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "bank_statement_line_id",
            ],
            [
                "bank_statement_lines.company_id",
                "bank_statement_lines.id",
            ],
            name=(
                "fk_bank_statement_reconciliations_"
                "statement_line"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "payment_id",
            ],
            [
                "payments.company_id",
                "payments.id",
            ],
            name=(
                "fk_bank_statement_reconciliations_payment"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=(
                "fk_bank_statement_reconciliations_created_by"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"],
            ["users.id"],
            name=(
                "fk_bank_statement_reconciliations_reversed_by"
            ),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_bank_statement_reconciliations_"
                "company_id_id"
            ),
        ),
        sa.CheckConstraint(
            "matched_amount > 0",
            name=(
                "ck_bank_statement_reconciliations_"
                "amount_positive"
            ),
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_bank_statement_reconciliations_"
                "currency_length"
            ),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'reversed')",
            name=(
                "ck_bank_statement_reconciliations_status"
            ),
        ),
        sa.CheckConstraint(
            (
                "("
                "status = 'active' "
                "AND reversed_by IS NULL "
                "AND reversed_at IS NULL"
                ") OR ("
                "status = 'reversed' "
                "AND reversed_by IS NOT NULL "
                "AND reversed_at IS NOT NULL"
                ")"
            ),
            name=(
                "ck_bank_statement_reconciliations_"
                "reversal_state"
            ),
        ),
    )

    op.create_index(
        "ix_bank_statement_reconciliations_company_id",
        "bank_statement_reconciliations",
        ["company_id"],
    )
    op.create_index(
        "ix_bank_statement_reconciliations_bank_statement_line_id",
        "bank_statement_reconciliations",
        ["bank_statement_line_id"],
    )
    op.create_index(
        "ix_bank_statement_reconciliations_payment_id",
        "bank_statement_reconciliations",
        ["payment_id"],
    )
    op.create_index(
        "ix_bank_statement_reconciliations_currency_code",
        "bank_statement_reconciliations",
        ["currency_code"],
    )
    op.create_index(
        "ix_bank_statement_reconciliations_status",
        "bank_statement_reconciliations",
        ["status"],
    )
    op.create_index(
        "uq_bank_statement_reconciliation_active_line",
        "bank_statement_reconciliations",
        [
            "company_id",
            "bank_statement_line_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active'"
        ),
    )
    op.create_index(
        "ix_bank_statement_reconciliation_payment_active",
        "bank_statement_reconciliations",
        [
            "company_id",
            "payment_id",
            "id",
        ],
        postgresql_where=sa.text(
            "status = 'active'"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bank_statement_reconciliation_payment_active",
        table_name="bank_statement_reconciliations",
    )
    op.drop_index(
        "uq_bank_statement_reconciliation_active_line",
        table_name="bank_statement_reconciliations",
    )
    op.drop_index(
        "ix_bank_statement_reconciliations_status",
        table_name="bank_statement_reconciliations",
    )
    op.drop_index(
        "ix_bank_statement_reconciliations_currency_code",
        table_name="bank_statement_reconciliations",
    )
    op.drop_index(
        "ix_bank_statement_reconciliations_payment_id",
        table_name="bank_statement_reconciliations",
    )
    op.drop_index(
        "ix_bank_statement_reconciliations_bank_statement_line_id",
        table_name="bank_statement_reconciliations",
    )
    op.drop_index(
        "ix_bank_statement_reconciliations_company_id",
        table_name="bank_statement_reconciliations",
    )
    op.drop_table(
        "bank_statement_reconciliations"
    )
