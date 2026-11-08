"""Protect encrypted payroll tax custody with independent ownership.

Revision ID: p13custrole261108
Revises: p13wtcust261107

Production deployment is deliberately separate from this delivery.
"""

from alembic import op

revision = "p13custrole261108"
down_revision = "p13wtcust261107"
branch_labels = None
depends_on = None


TABLES = (
    "payroll_payment_tax_secure_custody",
    "payroll_payment_tax_document_integrity",
    "payroll_payment_tax_document_reviews",
)


def upgrade():
    op.execute("""
        DO $$
        DECLARE
            table_name text;
            sequence_name text;
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname='erp_snapshot_owner'
                  AND NOT rolcanlogin
                  AND NOT rolsuper
                  AND NOT rolbypassrls
            ) THEN
                RAISE EXCEPTION
                    'Independent snapshot owner unavailable';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname='erp_user'
                  AND rolcanlogin
                  AND NOT rolsuper
                  AND NOT rolbypassrls
            ) THEN
                RAISE EXCEPTION
                    'Restricted application role unavailable';
            END IF;

            IF pg_has_role(
                'erp_user',
                'erp_snapshot_owner',
                'MEMBER'
            ) THEN
                RAISE EXCEPTION
                    'Application role belongs to owner role';
            END IF;

            FOREACH table_name IN ARRAY ARRAY[
                'payroll_payment_tax_secure_custody',
                'payroll_payment_tax_document_integrity',
                'payroll_payment_tax_document_reviews'
            ]
            LOOP
                IF to_regclass(
                    format('public.%I',table_name)
                ) IS NULL THEN
                    RAISE EXCEPTION
                        'Required custody table missing: %',
                        table_name;
                END IF;
            END LOOP;
        END
        $$;
    """)

    op.execute("""
        CREATE FUNCTION public.p13_custody_forbid_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            RAISE EXCEPTION
                'Immutable custody records cannot be changed'
                USING ERRCODE='42501';
        END;
        $$;
    """)

    for table in TABLES:
        op.execute(f"""
            CREATE TRIGGER trg_p13_custody_immutable
            BEFORE UPDATE OR DELETE
            ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION
                public.p13_custody_forbid_mutation();
        """)

        op.execute(f"""
            ALTER TABLE public.{table}
            OWNER TO erp_snapshot_owner;
        """)

        op.execute(f"""
            REVOKE ALL ON TABLE public.{table}
            FROM PUBLIC, erp_user;
        """)

        op.execute(f"""
            GRANT SELECT, INSERT
            ON TABLE public.{table}
            TO erp_user;
        """)

        # Permit inserts into the serial/identity sequence
        # without granting table ownership or DDL rights.
        op.execute(f"""
            DO $$
            DECLARE
                sequence_name text;
            BEGIN
                SELECT pg_get_serial_sequence(
                    'public.{table}',
                    'id'
                ) INTO sequence_name;

                IF sequence_name IS NOT NULL THEN
                    EXECUTE format(
                        'GRANT USAGE, SELECT ON SEQUENCE %s TO erp_user',
                        sequence_name
                    );
                END IF;
            END
            $$;
        """)

    op.execute("""
        REVOKE ALL ON FUNCTION
        public.p13_custody_forbid_mutation()
        FROM PUBLIC;
    """)


def downgrade():
    raise RuntimeError(
        "Security ownership and immutable custody cannot "
        "be downgraded automatically. Use a separately "
        "reviewed recovery migration."
    )
