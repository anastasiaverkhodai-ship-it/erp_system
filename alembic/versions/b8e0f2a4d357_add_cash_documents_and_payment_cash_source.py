"""add cash documents and payment cash source

Revision ID: b8e0f2a4d357
Revises: a7d9e1f3c246
"""

from alembic import op
import sqlalchemy as sa


revision: str = "b8e0f2a4d357"
down_revision: str | None = "a7d9e1f3c246"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "cash_desk_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_payments_company_cash_desk",
        "payments",
        "cash_desks",
        ["company_id", "cash_desk_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_payments_single_money_source",
        "payments",
        "bank_account_id IS NULL OR cash_desk_id IS NULL",
    )

    op.create_index(
        "ix_payments_cash_desk_id",
        "payments",
        ["cash_desk_id"],
        unique=False,
    )

    op.create_table(
        "cash_documents",
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
            "cash_desk_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "payment_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "document_number",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "direction",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "document_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "amount",
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
            "counterparty_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "contract_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "external_reference",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "description",
            sa.String(length=500),
            nullable=True,
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
        sa.CheckConstraint(
            "direction IN ('incoming', 'outgoing')",
            name="ck_cash_documents_direction",
        ),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_cash_documents_amount_positive",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_cash_documents_currency_length",
        ),
        sa.CheckConstraint(
            "length(trim(document_number)) > 0",
            name="ck_cash_documents_number_nonempty",
        ),
        sa.CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name="ck_cash_documents_not_self_reversal",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_cash_documents_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_cash_documents_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "cash_desk_id"],
            ["cash_desks.company_id", "cash_desks.id"],
            name="fk_cash_documents_company_cash_desk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "payment_id"],
            ["payments.company_id", "payments.id"],
            name="fk_cash_documents_company_payment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "counterparty_id"],
            [
                "counterparties.company_id",
                "counterparties.id",
            ],
            name="fk_cash_documents_company_counterparty",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "counterparty_id",
                "contract_id",
            ],
            [
                "contracts.company_id",
                "contracts.counterparty_id",
                "contracts.id",
            ],
            name="fk_cash_documents_company_counterparty_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "cash_desk_id",
                "payment_id",
            ],
            [
                "cash_documents.company_id",
                "cash_documents.id",
                "cash_documents.cash_desk_id",
                "cash_documents.payment_id",
            ],
            name="fk_cash_documents_reversal_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_cash_documents_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "direction",
            "document_number",
            name="uq_cash_documents_company_direction_number",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            "cash_desk_id",
            "payment_id",
            name="uq_cash_documents_identity",
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_cash_documents_reversal_of_id",
        ),
    )

    indexes = (
        ("ix_cash_documents_company_id", ["company_id"]),
        ("ix_cash_documents_cash_desk_id", ["cash_desk_id"]),
        ("ix_cash_documents_payment_id", ["payment_id"]),
        ("ix_cash_documents_direction", ["direction"]),
        ("ix_cash_documents_document_date", ["document_date"]),
        ("ix_cash_documents_currency_code", ["currency_code"]),
        (
            "ix_cash_documents_counterparty_id",
            ["counterparty_id"],
        ),
        (
            "ix_cash_documents_contract_id",
            ["contract_id"],
        ),
        (
            "ix_cash_documents_external_reference",
            ["external_reference"],
        ),
        (
            "ix_cash_documents_reversal_of_id",
            ["reversal_of_id"],
        ),
    )

    for name, columns in indexes:
        op.create_index(
            name,
            "cash_documents",
            columns,
            unique=False,
        )


def downgrade() -> None:
    connection = op.get_bind()

    cash_document_count = connection.execute(
        sa.text(
            "SELECT count(*) FROM cash_documents"
        )
    ).scalar_one()

    cash_payment_count = connection.execute(
        sa.text(
            "SELECT count(*) "
            "FROM payments "
            "WHERE cash_desk_id IS NOT NULL"
        )
    ).scalar_one()

    if cash_document_count or cash_payment_count:
        raise RuntimeError(
            "Cannot downgrade b8e0f2a4d357: "
            "cash provenance exists and downgrade would "
            "destroy CashDocument/Payment source history"
        )

    op.drop_table("cash_documents")

    op.drop_index(
        "ix_payments_cash_desk_id",
        table_name="payments",
    )

    op.drop_constraint(
        "ck_payments_single_money_source",
        "payments",
        type_="check",
    )

    op.drop_constraint(
        "fk_payments_company_cash_desk",
        "payments",
        type_="foreignkey",
    )

    op.drop_column(
        "payments",
        "cash_desk_id",
    )
