"""Immutable independent severance review attestations.

Revision ID: p13sev261103d
Revises: p13sev261103c
"""

from alembic import op
import sqlalchemy as sa

revision = "p13sev261103d"
down_revision = "p13sev261103c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_severance_independent_reviews",

        sa.Column(
            "id", sa.Integer(), sa.Identity(),
            primary_key=True,
        ),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "financial_proposal_id",
            sa.Integer(), nullable=False,
        ),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(120), nullable=False),
        sa.Column(
            "request_fingerprint",
            sa.String(64), nullable=False,
        ),
        sa.Column(
            "review_reference",
            sa.String(500), nullable=False,
        ),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column(
            "decision",
            sa.String(40), nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),

        sa.ForeignKeyConstraint(
            ["company_id", "financial_proposal_id"],
            [
                "payroll_severance_financial_proposals.company_id",
                "payroll_severance_financial_proposals.id",
            ],
            name="fk_sev_review_proposal",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name="fk_sev_review_user",
            ondelete="RESTRICT",
        ),

        sa.UniqueConstraint(
            "company_id", "financial_proposal_id",
            name="uq_sev_review_proposal",
        ),

        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_sev_review_request",
        ),

        sa.CheckConstraint(
            "decision IN "
            "('reviewed_pending_statutory', 'rejected')",
            name="ck_sev_review_decision",
        ),

        sa.CheckConstraint(
            "length(trim(review_reference)) >= 10",
            name="ck_sev_review_reference",
        ),
    )

    op.execute("""
        CREATE FUNCTION guard_severance_independent_review()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            submitted_actor integer;
            proposal_document_date date;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION
                    'Independent severance review is immutable';
            END IF;

            SELECT p.submitted_by, p.document_date
            INTO submitted_actor, proposal_document_date
            FROM payroll_severance_financial_proposals p
            WHERE p.company_id = NEW.company_id
              AND p.id = NEW.financial_proposal_id
              AND p.status = 'review_required';

            IF submitted_actor IS NULL THEN
                RAISE EXCEPTION
                    'Active financial proposal required';
            END IF;

            IF NEW.reviewer_id = submitted_actor THEN
                RAISE EXCEPTION
                    'Submitter cannot review own severance proposal';
            END IF;

            IF NEW.review_date < proposal_document_date THEN
                RAISE EXCEPTION
                    'Review date precedes proposal evidence';
            END IF;

            RETURN NEW;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_sev_independent_review_guard
        BEFORE INSERT OR UPDATE OR DELETE
        ON payroll_severance_independent_reviews
        FOR EACH ROW
        EXECUTE FUNCTION guard_severance_independent_review();
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_sev_independent_review_guard
        ON payroll_severance_independent_reviews
    """)

    op.execute(
        "DROP FUNCTION guard_severance_independent_review()"
    )

    op.drop_table(
        "payroll_severance_independent_reviews"
    )
