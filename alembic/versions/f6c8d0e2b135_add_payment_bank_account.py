"""add payment bank account source

Revision ID: f6c8d0e2b135
Revises: e5b7c9d1a024
"""

from alembic import op
import sqlalchemy as sa


revision = "f6c8d0e2b135"
down_revision = "e5b7c9d1a024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "bank_account_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_payments_company_bank_account",
        "payments",
        "bank_accounts",
        ["company_id", "bank_account_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_index(
        "ix_payments_bank_account_id",
        "payments",
        ["bank_account_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()

    sourced_payment_count = connection.execute(
        sa.text(
            """
            SELECT count(*)
            FROM payments
            WHERE bank_account_id IS NOT NULL
            """
        )
    ).scalar_one()

    if sourced_payment_count:
        raise RuntimeError(
            "Cannot downgrade payment bank-account source "
            "after Payments with bank_account_id exist. "
            "Dropping the column would destroy historical "
            "bank source provenance."
        )

    op.drop_index(
        "ix_payments_bank_account_id",
        table_name="payments",
    )

    op.drop_constraint(
        "fk_payments_company_bank_account",
        "payments",
        type_="foreignkey",
    )

    op.drop_column(
        "payments",
        "bank_account_id",
    )
