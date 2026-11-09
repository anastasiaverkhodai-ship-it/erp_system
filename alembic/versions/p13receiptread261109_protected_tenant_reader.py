"""Review-only tenant receipt reader under an isolated DB role.

Revision ID: p13receiptread261109
Revises: p13receiptlock261109

No API activation or issuer/withholding approval.
The dedicated role is NOLOGIN and is not granted to erp_user.
"""

from alembic import op

revision = "p13receiptread261109"
down_revision = "p13receiptlock261109"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_roles
            WHERE rolname='erp_receipt_reader'
          ) THEN
            CREATE ROLE erp_receipt_reader
              NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT;
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM pg_roles
            WHERE rolname='erp_receipt_reader'
              AND NOT rolcanlogin
              AND NOT rolsuper
              AND NOT rolbypassrls
          ) THEN
            RAISE EXCEPTION 'Unsafe receipt reader role';
          END IF;
        END
        $$
    """)

    # Protected function owner needs source access.
    # The application role receives none of these grants.
    op.execute("""
        GRANT SELECT ON TABLE
          public.users,
          public.user_company_roles,
          public.role_permissions,
          public.permissions,
          public.payroll_external_receipt_lifecycle
        TO erp_snapshot_owner
    """)

    op.execute("""
        CREATE FUNCTION public.p13_receipt_tenant_review(
            p_actor_id integer,
            p_company_id integer,
            p_limit integer
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            v_allowed boolean;
            v_records jsonb;
        BEGIN
            IF p_actor_id IS NULL OR p_actor_id <= 0
               OR p_company_id IS NULL OR p_company_id <= 0
               OR p_limit IS NULL
               OR p_limit < 1 OR p_limit > 500
            THEN
                RAISE EXCEPTION 'Invalid tenant reader request'
                    USING ERRCODE = '22023';
            END IF;

            SELECT EXISTS (
                SELECT 1
                FROM public.users u
                JOIN public.user_company_roles ucr
                  ON ucr.user_id = u.id
                JOIN public.role_permissions rp
                  ON rp.role_id = ucr.role_id
                JOIN public.permissions perm
                  ON perm.id = rp.permission_id
                WHERE u.id = p_actor_id
                  AND u.is_active IS TRUE
                  AND ucr.company_id = p_company_id
                  AND ucr.is_active IS TRUE
                  AND perm.name = 'employees.manage'
            )
            INTO v_allowed;

            IF NOT v_allowed THEN
                RAISE EXCEPTION 'Company receipt access denied'
                    USING ERRCODE = '42501';
            END IF;

            SELECT COALESCE(
                jsonb_agg(to_jsonb(r)),
                '[]'::jsonb
            )
            INTO v_records
            FROM (
                SELECT *
                FROM public.payroll_external_receipt_lifecycle
                WHERE company_id = p_company_id
                LIMIT p_limit
            ) r;

            RETURN jsonb_build_object(
                'company_id', p_company_id,
                'records', v_records,
                'issuer_identity', 'NOT_VERIFIED',
                'actual_withholding', 'NOT_VERIFIED',
                'official_generation', 'BLOCKED'
            );
        END;
        $function$
    """)

    op.execute("""
        ALTER FUNCTION public.p13_receipt_tenant_review(
          integer, integer, integer
        ) OWNER TO erp_snapshot_owner
    """)

    op.execute("""
        REVOKE ALL ON FUNCTION
          public.p13_receipt_tenant_review(
            integer, integer, integer
          )
        FROM PUBLIC, erp_user
    """)

    op.execute("""
        GRANT USAGE ON SCHEMA public
        TO erp_receipt_reader
    """)

    op.execute("""
        GRANT EXECUTE ON FUNCTION
          public.p13_receipt_tenant_review(
            integer, integer, integer
          )
        TO erp_receipt_reader
    """)

    op.execute("""
        REVOKE ALL ON TABLE
          public.payroll_external_receipt_lifecycle
        FROM PUBLIC, erp_user, erp_receipt_reader
    """)


def downgrade():
    raise RuntimeError(
        "Protected receipt reader requires reviewed recovery"
    )
