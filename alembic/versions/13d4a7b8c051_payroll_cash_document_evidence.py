"""Payroll-specific cash document evidence.

Revision ID: 13d4a7b8c051
Revises: 13d4a7b8c050

Does not create a Payment or any additional journal.
Documents must reference a company-scoped cash payroll payout.
"""

from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c051"
down_revision = "13d4a7b8c050"
branch_labels = None
depends_on = None


def upgrade():
    # Required for an exact, company-scoped cash-desk/payroll link.
    op.create_unique_constraint(
        "uq_payroll_disbursements_company_id_cash_desk",
        "payroll_disbursements",
        ["company_id", "id", "cash_desk_id"],
    )

    op.create_table(
        "payroll_cash_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("cash_desk_id", sa.Integer(), nullable=False),
        sa.Column(
            "payroll_disbursement_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "document_number",
            sa.String(100),
            nullable=False,
        ),
        sa.Column(
            "event_kind",
            sa.String(20),
            nullable=False,
        ),
        sa.Column("document_date", sa.Date(), nullable=False),
        sa.Column(
            "amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(3),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("reversal_of_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_payroll_cash_docs_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_payroll_cash_docs_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "cash_desk_id"],
            ["cash_desks.company_id", "cash_desks.id"],
            name="fk_payroll_cash_docs_company_desk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "payroll_disbursement_id",
                "cash_desk_id",
            ],
            [
                "payroll_disbursements.company_id",
                "payroll_disbursements.id",
                "payroll_disbursements.cash_desk_id",
            ],
            name="fk_payroll_cash_docs_payout_identity",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "id",
            "cash_desk_id", "payroll_disbursement_id",
            name="uq_payroll_cash_docs_identity",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id", "reversal_of_id",
                "cash_desk_id", "payroll_disbursement_id",
            ],
            [
                "payroll_cash_documents.company_id",
                "payroll_cash_documents.id",
                "payroll_cash_documents.cash_desk_id",
                "payroll_cash_documents.payroll_disbursement_id",
            ],
            name="fk_payroll_cash_docs_reversal_identity",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "document_number",
            name="uq_payroll_cash_docs_number",
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_payroll_cash_docs_single_reversal",
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount <> 'NaN'::numeric",
            name="ck_payroll_cash_docs_positive_amount",
        ),
        sa.CheckConstraint(
            "event_kind IN ('issue', 'reversal')",
            name="ck_payroll_cash_docs_event_kind",
        ),
        sa.CheckConstraint(
            "(event_kind = 'issue' AND reversal_of_id IS NULL)"
            " OR (event_kind = 'reversal' "
            "AND reversal_of_id IS NOT NULL)",
            name="ck_payroll_cash_docs_event_identity",
        ),
        sa.CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name="ck_payroll_cash_docs_not_self_reversal",
        ),
        sa.CheckConstraint(
            "length(trim(document_number)) > 0",
            name="ck_payroll_cash_docs_number_nonempty",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_payroll_cash_docs_currency_length",
        ),
    )

    op.create_index(
        "ix_payroll_cash_docs_company_date_desk",
        "payroll_cash_documents",
        ["company_id", "document_date", "cash_desk_id"],
    )

    op.create_index(
        "uq_payroll_cash_docs_original_payout",
        "payroll_cash_documents",
        ["company_id", "payroll_disbursement_id"],
        unique=True,
        postgresql_where=sa.text("event_kind = 'issue'"),
    )

    # Historical document evidence is append-only.
    op.execute("""
        CREATE FUNCTION payroll_cash_document_readonly()
        RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                'Payroll cash document evidence is immutable';
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_payroll_cash_document_readonly
        BEFORE UPDATE OR DELETE ON payroll_cash_documents
        FOR EACH ROW
        EXECUTE FUNCTION payroll_cash_document_readonly();
    """)


def downgrade():
    connection = op.get_bind()

    existing = connection.execute(
        sa.text(
            "SELECT count(*) FROM payroll_cash_documents"
        )
    ).scalar_one()

    if existing:
        raise RuntimeError(
            "Cannot downgrade c051: preserve payroll cash "
            "document history before changing schema"
        )

    op.execute(
        "DROP TRIGGER trg_payroll_cash_document_readonly "
        "ON payroll_cash_documents"
    )
    op.execute(
        "DROP FUNCTION payroll_cash_document_readonly()"
    )
    op.drop_index(
        "uq_payroll_cash_docs_original_payout",
        table_name="payroll_cash_documents",
    )
    op.drop_index(
        "ix_payroll_cash_docs_company_date_desk",
        table_name="payroll_cash_documents",
    )
    op.drop_table("payroll_cash_documents")
    op.drop_constraint(
        "uq_payroll_disbursements_company_id_cash_desk",
        "payroll_disbursements",
        type_="unique",
    )
