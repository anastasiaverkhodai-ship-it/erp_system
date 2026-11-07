"""Persist immutable, unverified payment tax evidence references.

Revision ID: p13wtevid261107
Revises: p13wtcorr261107
"""

from alembic import op
import sqlalchemy as sa

revision = "p13wtevid261107"
down_revision = "p13wtcorr261107"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_payment_tax_evidence",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("candidate_id", sa.Integer, nullable=False),
        sa.Column("correction_id", sa.Integer),
        sa.Column("version_key", sa.String(80), nullable=False),
        sa.Column("payroll_disbursement_id", sa.Integer, nullable=False),
        sa.Column("statutory_line_id", sa.Integer, nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("payment_date", sa.Date, nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_issuer", sa.String(200), nullable=False),
        sa.Column("evidence_reference", sa.String(500), nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(150), nullable=False),
        sa.Column(
            "verification_status", sa.String(20),
            nullable=False, server_default="unverified",
        ),
        sa.Column("created_by", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "candidate_id"],
            [
                "payroll_payment_tax_allocation_candidates.company_id",
                "payroll_payment_tax_allocation_candidates.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "correction_id"],
            [
                "payroll_payment_tax_allocation_corrections.company_id",
                "payroll_payment_tax_allocation_corrections.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "payroll_disbursement_id"],
            [
                "payroll_disbursements.company_id",
                "payroll_disbursements.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "statutory_line_id"],
            [
                "payroll_statutory_result_lines.company_id",
                "payroll_statutory_result_lines.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "id",
            name="uq_pptae_company_id",
        ),
        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_pptae_request",
        ),
        sa.UniqueConstraint(
            "company_id", "candidate_id", "version_key",
            name="uq_pptae_candidate_version",
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount <> 'NaN'::numeric",
            name="ck_pptae_positive",
        ),
        sa.CheckConstraint(
            "verification_status = 'unverified'",
            name="ck_pptae_never_approved",
        ),
        sa.CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pptae_digest",
        ),
        sa.CheckConstraint(
            "length(btrim(source_type)) > 0 "
            "AND length(btrim(source_issuer)) > 0 "
            "AND length(btrim(evidence_reference)) > 0 "
            "AND length(btrim(request_key)) > 0",
            name="ck_pptae_references",
        ),
    )

    op.execute("""
        CREATE FUNCTION pptae_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                'Payment tax evidence is append-only';
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_pptae_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_payment_tax_evidence
        FOR EACH ROW
        EXECUTE FUNCTION pptae_reject_mutation()
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_pptae_immutable
        ON payroll_payment_tax_evidence
    """)
    op.drop_table("payroll_payment_tax_evidence")
    op.execute("DROP FUNCTION pptae_reject_mutation()")
