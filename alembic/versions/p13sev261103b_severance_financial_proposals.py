"""Immutable severance financial correction proposals.

Revision ID: p13sev261103b
Revises: p13sev261103a
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "p13sev261103b"
down_revision = "p13sev261103a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_sev_case_company_id",
        "payroll_severance_correction_cases",
        ["company_id", "id"],
    )

    op.create_table(
        "payroll_severance_financial_proposals",

        sa.Column(
            "id", sa.Integer(),
            sa.Identity(), primary_key=True,
        ),
        sa.Column(
            "company_id", sa.Integer(), nullable=False,
        ),
        sa.Column(
            "correction_case_id", sa.Integer(), nullable=False,
        ),
        sa.Column(
            "request_key", sa.String(120), nullable=False,
        ),
        sa.Column(
            "request_fingerprint", sa.String(64), nullable=False,
        ),
        sa.Column(
            "document_reference", sa.String(500), nullable=False,
        ),
        sa.Column(
            "document_date", sa.Date(), nullable=False,
        ),
        sa.Column(
            "original_financial", JSONB(), nullable=False,
        ),
        sa.Column(
            "proposed_financial", JSONB(), nullable=False,
        ),
        sa.Column(
            "status", sa.String(32),
            nullable=False,
            server_default="review_required",
        ),
        sa.Column(
            "submitted_by", sa.Integer(), nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),

        sa.ForeignKeyConstraint(
            ["company_id", "correction_case_id"],
            [
                "payroll_severance_correction_cases.company_id",
                "payroll_severance_correction_cases.id",
            ],
            name="fk_sev_financial_proposal_case",
            ondelete="RESTRICT",
        ),

        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_sev_financial_proposal_request",
        ),

        sa.UniqueConstraint(
            "company_id", "correction_case_id",
            name="uq_sev_financial_proposal_case",
        ),

        sa.CheckConstraint(
            "status = 'review_required'",
            name="ck_sev_financial_proposal_review_only",
        ),

        sa.CheckConstraint(
            "length(trim(document_reference)) >= 10",
            name="ck_sev_financial_proposal_document",
        ),
    )

    # Both tables are append-only at the PostgreSQL layer.
    # No UPDATE/DELETE can silently rewrite settlement provenance.
    op.execute("""
        CREATE FUNCTION reject_severance_case_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
              'Severance correction evidence is immutable';
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_sev_case_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_severance_correction_cases
        FOR EACH ROW
        EXECUTE FUNCTION reject_severance_case_mutation();
    """)

    op.execute("""
        CREATE TRIGGER trg_sev_proposal_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_severance_financial_proposals
        FOR EACH ROW
        EXECUTE FUNCTION reject_severance_case_mutation();
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_sev_proposal_immutable
        ON payroll_severance_financial_proposals
    """)

    op.execute("""
        DROP TRIGGER trg_sev_case_immutable
        ON payroll_severance_correction_cases
    """)

    op.execute(
        "DROP FUNCTION reject_severance_case_mutation()"
    )

    op.drop_table(
        "payroll_severance_financial_proposals"
    )

    op.drop_constraint(
        "uq_sev_case_company_id",
        "payroll_severance_correction_cases",
        type_="unique",
    )
