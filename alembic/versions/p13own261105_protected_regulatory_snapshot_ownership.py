"""Protect ownership of internal regulatory snapshots.

Revision ID: p13own261105
Revises: p13regint261105

The protected table, its linked identity sequence, and
its immutable trigger function must not belong to the
ordinary ERP application role.
"""

from alembic import op
import sqlalchemy as sa


revision = "p13own261105"
down_revision = "p13regint261105"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    state = bind.execute(
        sa.text("""
            SELECT
                current_user,
                EXISTS (
                    SELECT 1
                    FROM pg_roles
                    WHERE rolname = 'erp_snapshot_owner'
                      AND NOT rolcanlogin
                ),
                pg_has_role(
                    'erp_user',
                    'erp_snapshot_owner',
                    'MEMBER'
                )
        """)
    ).one()

    active_role, owner_exists, app_is_owner_member = state

    if active_role == "erp_user":
        raise RuntimeError(
            "Protected regulatory migration must not run "
            "as the application role"
        )

    if not owner_exists:
        raise RuntimeError(
            "Protected owner role is missing or LOGIN-enabled"
        )

    if app_is_owner_member:
        raise RuntimeError(
            "Application role must not inherit owner rights"
        )

    # Changing table owner transfers its linked IDENTITY
    # sequence automatically. Do not ALTER that sequence
    # separately.
    op.execute("""
        ALTER TABLE
            public.payroll_regulatory_internal_snapshots
        OWNER TO erp_snapshot_owner
    """)

    op.execute("""
        ALTER FUNCTION
            public.protect_regulatory_internal_snapshot()
        OWNER TO erp_snapshot_owner
    """)

    # Immutable evidence supports reading and append-only
    # insertion, not application-driven update/delete.
    op.execute("""
        REVOKE UPDATE, DELETE, TRUNCATE
        ON TABLE public.payroll_regulatory_internal_snapshots
        FROM PUBLIC, erp_user
    """)

    op.execute("""
        GRANT SELECT, INSERT
        ON TABLE public.payroll_regulatory_internal_snapshots
        TO erp_user
    """)

    op.execute("""
        GRANT USAGE, SELECT
        ON SEQUENCE
            public.payroll_regulatory_internal_snapshots_id_seq
        TO erp_user
    """)

    op.execute("""
        DO $check$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_class c
                WHERE c.oid IN (
                    'public.payroll_regulatory_internal_snapshots'
                        ::regclass,
                    'public.payroll_regulatory_internal_snapshots_id_seq'
                        ::regclass
                )
                AND pg_get_userbyid(c.relowner)
                    <> 'erp_snapshot_owner'
            ) THEN
                RAISE EXCEPTION
                    'Protected table/sequence owner mismatch';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_proc p
                WHERE p.oid =
                  'public.protect_regulatory_internal_snapshot()'
                      ::regprocedure
                  AND pg_get_userbyid(p.proowner)
                      = 'erp_snapshot_owner'
            ) THEN
                RAISE EXCEPTION
                    'Immutable trigger function owner mismatch';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_trigger t
                WHERE t.tgrelid =
                    'public.payroll_regulatory_internal_snapshots'
                        ::regclass
                  AND NOT t.tgisinternal
                  AND t.tgenabled <> 'O'
            ) THEN
                RAISE EXCEPTION
                    'Protected trigger disabled';
            END IF;
        END;
        $check$;
    """)


def downgrade():
    # A security hardening revision must not silently
    # return ownership to the runtime application role.
    raise RuntimeError(
        "Protected owner separation cannot be "
        "downgraded automatically"
    )
