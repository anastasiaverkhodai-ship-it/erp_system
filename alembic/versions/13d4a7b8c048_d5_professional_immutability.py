"""Enforce append-only professional evidence in PostgreSQL.

Revision ID: 13d4a7b8c048
Revises: 13d4a7b8c047
"""
from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c048"
down_revision = "13d4a7b8c047"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION prevent_d5_professional_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'D5 professional evidence is immutable'
                USING ERRCODE = '23514';
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_d5_professional_evidence_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_d5_professional_evidence
        FOR EACH ROW
        EXECUTE FUNCTION
            prevent_d5_professional_evidence_mutation()
    """)


def downgrade():
    bind = op.get_bind()

    existing = bind.execute(sa.text("""
        SELECT count(*)
        FROM payroll_d5_professional_evidence
    """)).scalar_one()

    if existing:
        raise RuntimeError(
            "Cannot remove immutability protection while "
            "professional evidence exists"
        )

    op.execute("""
        DROP TRIGGER IF EXISTS
            trg_d5_professional_evidence_immutable
        ON payroll_d5_professional_evidence
    """)

    op.execute("""
        DROP FUNCTION IF EXISTS
            prevent_d5_professional_evidence_mutation()
    """)
