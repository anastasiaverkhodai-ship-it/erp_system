-- ERP Phase 13: isolated-copy ownership separation proof.
--
-- NEVER apply to the production/source database.
-- Requires a privileged PostgreSQL maintenance session.
-- No DROP TABLE, TRUNCATE or schema reconstruction.
--
-- This is a tested security procedure, not authorization
-- for production deployment.

BEGIN;

DO $guard$
DECLARE
    privileged boolean;
BEGIN
    IF current_database() = 'erp_db' THEN
        RAISE EXCEPTION
            'Source ERP database modification forbidden';
    END IF;

    SELECT rolsuper
    INTO privileged
    FROM pg_roles
    WHERE rolname = current_user;

    IF privileged IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION
            'Privileged isolated maintenance role required';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'erp_snapshot_owner'
    ) THEN
        RAISE EXCEPTION
            'Existing isolated snapshot owner role missing';
    END IF;

    IF pg_has_role(
        'erp_user', 'erp_snapshot_owner', 'MEMBER'
    ) THEN
        RAISE EXCEPTION
            'Runtime application role inherits protected owner';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'erp_user'
          AND NOT rolsuper
          AND NOT rolcreaterole
          AND NOT rolbypassrls
    ) THEN
        RAISE EXCEPTION
            'Runtime application role privileges unsafe';
    END IF;
END;
$guard$;

-- Separate all existing payroll/regulatory protected
-- tables. Preserve their data, constraints and triggers.
DO $tables$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.oid::regclass AS object_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
          AND (
              c.relname LIKE 'payroll_regulatory_%'
              OR c.relname LIKE 'payroll_statutory_%'
              OR c.relname LIKE 'payroll_tax_remittance%'
          )
          AND pg_get_userbyid(c.relowner) = 'erp_user'
        ORDER BY c.relname
    LOOP
        EXECUTE format(
            'ALTER TABLE %s OWNER TO erp_snapshot_owner',
            r.object_name
        );
    END LOOP;
END;
$tables$;

-- Transfer any remaining independently owned sequences.
DO $sequences$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.oid::regclass AS object_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'S'
          AND (
              c.relname LIKE 'payroll_regulatory_%'
              OR c.relname LIKE 'payroll_statutory_%'
              OR c.relname LIKE 'payroll_tax_remittance%'
          )
          AND pg_get_userbyid(c.relowner) = 'erp_user'
    LOOP
        EXECUTE format(
            'ALTER SEQUENCE %s OWNER TO erp_snapshot_owner',
            r.object_name
        );
    END LOOP;
END;
$sequences$;

-- Protected trigger functions must not remain owned
-- by the ordinary application role.
DO $functions$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT DISTINCT p.oid::regprocedure AS routine_name
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE n.nspname = 'public'
          AND NOT t.tgisinternal
          AND (
              c.relname LIKE 'payroll_regulatory_%'
              OR c.relname LIKE 'payroll_statutory_%'
              OR c.relname LIKE 'payroll_tax_remittance%'
          )
          AND pg_get_userbyid(p.proowner) = 'erp_user'
    LOOP
        EXECUTE format(
            'ALTER FUNCTION %s OWNER TO erp_snapshot_owner',
            r.routine_name
        );
    END LOOP;
END;
$functions$;

-- Preserve normal runtime DML, not ownership or DDL.
DO $grants$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.oid::regclass AS object_name, c.relkind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND (
              c.relname LIKE 'payroll_regulatory_%'
              OR c.relname LIKE 'payroll_statutory_%'
              OR c.relname LIKE 'payroll_tax_remittance%'
          )
          AND c.relkind IN ('r', 'p', 'S')
    LOOP
        IF r.relkind = 'S' THEN
            EXECUTE format(
                'GRANT USAGE, SELECT ON SEQUENCE %s TO erp_user',
                r.object_name
            );
        ELSE
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE '
                'ON TABLE %s TO erp_user',
                r.object_name
            );
        END IF;
    END LOOP;
END;
$grants$;

-- Fail closed if any protected object remains
-- owned by application role.
DO $final$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'S')
          AND pg_get_userbyid(c.relowner) = 'erp_user'
          AND (
              c.relname LIKE 'payroll_regulatory_%'
              OR c.relname LIKE 'payroll_statutory_%'
              OR c.relname LIKE 'payroll_tax_remittance%'
          )
    ) THEN
        RAISE EXCEPTION
            'Protected objects still owned by runtime role';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE n.nspname = 'public'
          AND NOT t.tgisinternal
          AND (
              c.relname LIKE 'payroll_regulatory_%'
              OR c.relname LIKE 'payroll_statutory_%'
              OR c.relname LIKE 'payroll_tax_remittance%'
          )
          AND pg_get_userbyid(p.proowner) = 'erp_user'
    ) THEN
        RAISE EXCEPTION
            'Protected trigger functions still runtime-owned';
    END IF;
END;
$final$;

COMMIT;
