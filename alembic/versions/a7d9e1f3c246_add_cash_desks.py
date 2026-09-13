"""add cash desks

Revision ID: a7d9e1f3c246
Revises: f6c8d0e2b135
"""

from alembic import op
import sqlalchemy as sa


revision: str = "a7d9e1f3c246"
down_revision: str | None = "f6c8d0e2b135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cash_desks",
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
            "code",
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
            name="ck_cash_desks_name_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(code)) > 0",
            name="ck_cash_desks_code_nonempty",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_cash_desks_currency_code_length",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_cash_desks_company",
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
            name="fk_cash_desks_company_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "code",
            name="uq_cash_desks_company_code",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_cash_desks_company_id_id",
        ),
    )

    op.create_index(
        "ix_cash_desks_company_id",
        "cash_desks",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_cash_desks_currency_code",
        "cash_desks",
        ["currency_code"],
        unique=False,
    )
    op.create_index(
        "ix_cash_desks_accounting_account_id",
        "cash_desks",
        ["accounting_account_id"],
        unique=False,
    )
    op.create_index(
        "ix_cash_desks_is_active",
        "cash_desks",
        ["is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cash_desks_is_active",
        table_name="cash_desks",
    )
    op.drop_index(
        "ix_cash_desks_accounting_account_id",
        table_name="cash_desks",
    )
    op.drop_index(
        "ix_cash_desks_currency_code",
        table_name="cash_desks",
    )
    op.drop_index(
        "ix_cash_desks_company_id",
        table_name="cash_desks",
    )
    op.drop_table("cash_desks")
