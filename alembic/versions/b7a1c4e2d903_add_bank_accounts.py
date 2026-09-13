"""add bank accounts

Revision ID: b7a1c4e2d903
Revises: f4c2a91b7e63
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b7a1c4e2d903"
down_revision: str | None = "f4c2a91b7e63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bank_accounts",
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
            "name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "account_number",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(length=3),
            server_default="UAH",
            nullable=False,
        ),
        sa.Column(
            "accounting_account_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_bank_accounts_name_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(account_number)) > 0",
            name="ck_bank_accounts_number_nonempty",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_bank_accounts_currency_code_length",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_bank_accounts_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "accounting_account_id",
            ],
            [
                "accounts.company_id",
                "accounts.id",
            ],
            name="fk_bank_accounts_company_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_bank_accounts",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_bank_accounts_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "account_number",
            name=(
                "uq_bank_accounts_"
                "company_account_number"
            ),
        ),
    )

    op.create_index(
        "ix_bank_accounts_company_id",
        "bank_accounts",
        ["company_id"],
        unique=False,
    )

    op.create_index(
        "ix_bank_accounts_currency_code",
        "bank_accounts",
        ["currency_code"],
        unique=False,
    )

    op.create_index(
        "ix_bank_accounts_accounting_account_id",
        "bank_accounts",
        ["accounting_account_id"],
        unique=False,
    )

    op.create_index(
        "ix_bank_accounts_is_active",
        "bank_accounts",
        ["is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bank_accounts_is_active",
        table_name="bank_accounts",
    )
    op.drop_index(
        "ix_bank_accounts_accounting_account_id",
        table_name="bank_accounts",
    )
    op.drop_index(
        "ix_bank_accounts_currency_code",
        table_name="bank_accounts",
    )
    op.drop_index(
        "ix_bank_accounts_company_id",
        table_name="bank_accounts",
    )
    op.drop_table("bank_accounts")
