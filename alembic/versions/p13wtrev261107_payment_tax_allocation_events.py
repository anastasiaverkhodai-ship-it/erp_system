"""Append-only review and revocation events for payment-tax drafts.

Revision ID: p13wtrev261107
Revises: p13wtalloc261107
"""

from alembic import op
import sqlalchemy as sa

revision = "p13wtrev261107"
down_revision = "p13wtalloc261107"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_payment_tax_allocation_candidate_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("candidate_id", sa.Integer, nullable=False),
        sa.Column("event_kind", sa.String(24), nullable=False),
        sa.Column("request_key", sa.String(150), nullable=False),
        sa.Column("evidence_reference", sa.String(500), nullable=False),
        sa.Column("actor_user_id", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
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
            ["actor_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "id",
            name="uq_pptace_company_id",
        ),
        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_pptace_request",
        ),
        sa.UniqueConstraint(
            "company_id", "candidate_id", "event_kind",
            name="uq_pptace_candidate_kind",
        ),
        sa.CheckConstraint(
            "event_kind IN ('reviewed','revoked')",
            name="ck_pptace_kind",
        ),
        sa.CheckConstraint(
            "length(btrim(request_key)) > 0 "
            "AND length(btrim(evidence_reference)) > 0",
            name="ck_pptace_evidence",
        ),
    )

    op.execute("""
        CREATE FUNCTION pptace_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'Payment tax allocation events are append-only';
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_pptace_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_payment_tax_allocation_candidate_events
        FOR EACH ROW
        EXECUTE FUNCTION pptace_reject_mutation()
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_pptace_immutable
        ON payroll_payment_tax_allocation_candidate_events
    """)
    op.drop_table(
        "payroll_payment_tax_allocation_candidate_events"
    )
    op.execute(
        "DROP FUNCTION pptace_reject_mutation()"
    )
