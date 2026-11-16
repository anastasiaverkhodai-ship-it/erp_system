"""Grant application access to immutable paid-income snapshots.

The snapshot remains append-only for the application.

Revision ID: p13paidacl261116
Revises: p13paidincome261115
"""

from alembic import op


revision = "p13paidacl261116"
down_revision = "p13paidincome261115"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        DO $$
        DECLARE
            seq_name text;
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = 'erp_user'
            ) THEN
                RAISE EXCEPTION
                    'Required application role erp_user is missing';
            END IF;

            IF to_regclass(
                'public.payroll_paid_income'
            ) IS NULL THEN
                RAISE EXCEPTION
                    'Required paid-income snapshot table is missing';
            END IF;

            EXECUTE
                'GRANT SELECT, INSERT '
                'ON TABLE public.payroll_paid_income '
                'TO erp_user';

            seq_name := pg_get_serial_sequence(
                'public.payroll_paid_income',
                'id'
            );

            IF seq_name IS NOT NULL THEN
                EXECUTE format(
                    'GRANT USAGE, SELECT ON SEQUENCE %s TO erp_user',
                    seq_name::regclass
                );
            END IF;
        END
        $$;
    """)


def downgrade():
    op.execute("""
        DO $$
        DECLARE
            seq_name text;
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname = 'erp_user'
            ) AND to_regclass(
                'public.payroll_paid_income'
            ) IS NOT NULL THEN

                EXECUTE
                    'REVOKE SELECT, INSERT '
                    'ON TABLE public.payroll_paid_income '
                    'FROM erp_user';

                seq_name := pg_get_serial_sequence(
                    'public.payroll_paid_income',
                    'id'
                );

                IF seq_name IS NOT NULL THEN
                    EXECUTE format(
                        'REVOKE USAGE, SELECT ON SEQUENCE %s '
                        'FROM erp_user',
                        seq_name::regclass
                    );
                END IF;
            END IF;
        END
        $$;
    """)
