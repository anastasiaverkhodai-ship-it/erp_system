"""Evidence for per-desk physical cash opening declarations.

Revision ID: 13d4a7b8c052
Revises: 13d4a7b8c051

This schema records a source declaration. It does NOT automatically
verify a cash count or authorize any cash expenditure.
"""

from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c052"
down_revision = "13d4a7b8c051"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cash_desk_opening_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("cash_desk_id", sa.Integer(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column(
            "declared_amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(3),
            nullable=False,
        ),
        sa.Column(
            "evidence_reference",
            sa.String(255),
            nullable=False,
        ),
        sa.Column("attested_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_cash_desk_open_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "cash_desk_id"],
            ["cash_desks.company_id", "cash_desks.id"],
            name="fk_cash_desk_open_desk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attested_by"],
            ["users.id"],
            name="fk_cash_desk_open_actor",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "cash_desk_id",
            name="uq_cash_desk_open_single_baseline",
        ),
        sa.CheckConstraint(
            "declared_amount >= 0 "
            "AND declared_amount <> 'NaN'::numeric",
            name="ck_cash_desk_open_amount",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_cash_desk_open_currency",
        ),
        sa.CheckConstraint(
            "length(trim(evidence_reference)) > 0",
            name="ck_cash_desk_open_evidence_reference",
        ),
    )

    op.create_index(
        "ix_cash_desk_open_company_effective_date",
        "cash_desk_opening_evidence",
        ["company_id", "effective_date"],
    )

    op.execute("""
        CREATE FUNCTION cash_desk_opening_readonly()
        RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                'Cash desk opening evidence is immutable';
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_cash_desk_opening_readonly
        BEFORE UPDATE OR DELETE ON cash_desk_opening_evidence
        FOR EACH ROW
        EXECUTE FUNCTION cash_desk_opening_readonly();
    """)


def downgrade():
    existing = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM cash_desk_opening_evidence"
        )
    ).scalar_one()

    if existing:
        raise RuntimeError(
            "Cannot downgrade c052: cash opening evidence exists"
        )

    op.execute(
        "DROP TRIGGER trg_cash_desk_opening_readonly "
        "ON cash_desk_opening_evidence"
    )
    op.execute(
        "DROP FUNCTION cash_desk_opening_readonly()"
    )
    op.drop_index(
        "ix_cash_desk_open_company_effective_date",
        table_name="cash_desk_opening_evidence",
    )
    op.drop_table("cash_desk_opening_evidence")
