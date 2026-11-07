"""Source-bound, append-only draft allocation corrections.

Revision ID: p13wtcorr261107
Revises: p13wtrev261107
"""

from alembic import op
import sqlalchemy as sa

revision = "p13wtcorr261107"
down_revision = "p13wtrev261107"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_payment_tax_allocation_corrections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("original_candidate_id", sa.Integer, nullable=False),
        sa.Column("previous_correction_id", sa.Integer),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("request_key", sa.String(150), nullable=False),
        sa.Column("evidence_reference", sa.String(500), nullable=False),
        sa.Column("created_by", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "original_candidate_id"],
            [
                "payroll_payment_tax_allocation_candidates.company_id",
                "payroll_payment_tax_allocation_candidates.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "previous_correction_id"],
            [
                "payroll_payment_tax_allocation_corrections.company_id",
                "payroll_payment_tax_allocation_corrections.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "id", name="uq_pptacorr_company_id",
        ),
        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_pptacorr_request",
        ),
        sa.UniqueConstraint(
            "company_id", "previous_correction_id",
            name="uq_pptacorr_predecessor",
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount <> 'NaN'::numeric",
            name="ck_pptacorr_amount",
        ),
        sa.CheckConstraint(
            "length(btrim(request_key)) > 0 "
            "AND length(btrim(evidence_reference)) > 0",
            name="ck_pptacorr_references",
        ),
    )

    op.create_index(
        "uq_pptacorr_first",
        "payroll_payment_tax_allocation_corrections",
        ["company_id", "original_candidate_id"],
        unique=True,
        postgresql_where=sa.text(
            "previous_correction_id IS NULL"
        ),
    )

    op.create_index(
        "ix_pptacorr_chain",
        "payroll_payment_tax_allocation_corrections",
        ["company_id", "original_candidate_id", "id"],
    )

    op.execute("""
        CREATE FUNCTION pptacorr_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                'Payment tax corrections are append-only';
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_pptacorr_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_payment_tax_allocation_corrections
        FOR EACH ROW
        EXECUTE FUNCTION pptacorr_reject_mutation()
    """)

    # Existing candidate rows must remain immutable as well.
    op.execute("""
        CREATE TRIGGER trg_pptac_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_payment_tax_allocation_candidates
        FOR EACH ROW
        EXECUTE FUNCTION pptacorr_reject_mutation()
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_pptac_immutable
        ON payroll_payment_tax_allocation_candidates
    """)
    op.execute("""
        DROP TRIGGER trg_pptacorr_immutable
        ON payroll_payment_tax_allocation_corrections
    """)
    op.drop_index(
        "ix_pptacorr_chain",
        table_name="payroll_payment_tax_allocation_corrections",
    )
    op.drop_index(
        "uq_pptacorr_first",
        table_name="payroll_payment_tax_allocation_corrections",
    )
    op.drop_table(
        "payroll_payment_tax_allocation_corrections"
    )
    op.execute("DROP FUNCTION pptacorr_reject_mutation()")
