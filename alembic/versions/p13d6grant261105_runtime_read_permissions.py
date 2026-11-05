"""Allow runtime to read persisted D6 evidence.

Revision ID: p13d6grant261105
Revises: p13own261105

This migration grants SELECT on the two existing D6
evidence tables. It does not change ownership or grant
DDL, UPDATE, DELETE, or TRUNCATE permissions.
"""

from alembic import op
import sqlalchemy as sa


revision = "p13d6grant261105"
down_revision = "p13own261105"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    state = bind.execute(
        sa.text("""
            SELECT
                current_user,
                (
                    SELECT rolsuper
                    FROM pg_roles
                    WHERE rolname = current_user
                ),
                EXISTS (
                    SELECT 1
                    FROM pg_roles
                    WHERE rolname = 'erp_user'
                )
        """)
    ).one()

    user, is_superuser, app_exists = state

    if user == "erp_user" or not is_superuser:
        raise RuntimeError(
            "D6 permission migration requires "
            "a separate maintenance superuser"
        )

    if not app_exists:
        raise RuntimeError(
            "ERP application role is missing"
        )

    op.execute("""
        GRANT SELECT
        ON TABLE
            public.payroll_d6_evidence,
            public.payroll_d6_evidence_reviews
        TO erp_user
    """)

    op.execute("""
        DO $verify$
        BEGIN
            IF NOT has_table_privilege(
                'erp_user',
                'public.payroll_d6_evidence',
                'SELECT'
            ) OR NOT has_table_privilege(
                'erp_user',
                'public.payroll_d6_evidence_reviews',
                'SELECT'
            ) THEN
                RAISE EXCEPTION
                    'D6 runtime SELECT permissions missing';
            END IF;

            IF pg_has_role(
                'erp_user',
                'erp_snapshot_owner',
                'MEMBER'
            ) THEN
                RAISE EXCEPTION
                    'Runtime role has protected ownership';
            END IF;
        END;
        $verify$;
    """)


def downgrade():
    # Do not silently revoke read rights from an
    # existing application workflow.
    raise RuntimeError(
        "D6 permission hardening must be reviewed "
        "before downgrade"
    )
