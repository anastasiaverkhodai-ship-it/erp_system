"""Real PostgreSQL role separation on an isolated upgraded copy."""

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.core.config import settings


def run_psql(database, sql):
    result = subprocess.run(
        [
            "psql",
            "-X",
            "-qAt",
            "-v", "ON_ERROR_STOP=1",
            "-d", database,
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )

    if result.returncode:
        raise AssertionError(
            "Isolated PostgreSQL security test failed:\n"
            + result.stderr[-5000:]
        )

    return result.stdout


@pytest.mark.asyncio
async def test_role_separation_on_isolated_copy():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    database = make_url(
        str(settings.database_url)
    ).database

    assert database
    assert database != "erp_db"

    identity = run_psql(
        database,
        "SELECT current_database();",
    ).strip()

    assert identity == database

    maintenance_user = run_psql(
        database,
        "SELECT current_user;",
    ).strip()

    assert maintenance_user != "erp_user"

    sql_path = Path(
        "scripts/sql/phase13_role_separation_isolated.sql"
    )

    assert sql_path.is_file()

    # psql opens the SAME isolated copy used by pytest.
    run_psql(database, sql_path.read_text())

    checks = run_psql(
        database,
        r"""
BEGIN;

DO $security$
DECLARE
    owned_count integer;
    wrong_functions integer;
BEGIN
    SELECT count(*)
    INTO owned_count
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'S')
      AND pg_get_userbyid(c.relowner) = 'erp_user'
      AND (
          c.relname LIKE 'payroll_regulatory_%'
          OR c.relname LIKE 'payroll_statutory_%'
          OR c.relname LIKE 'payroll_tax_remittance%'
      );

    IF owned_count != 0 THEN
        RAISE EXCEPTION
            'Application still owns protected objects';
    END IF;

    SELECT count(*)
    INTO wrong_functions
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_proc p ON p.oid = t.tgfoid
    WHERE n.nspname = 'public'
      AND NOT t.tgisinternal
      AND pg_get_userbyid(p.proowner) = 'erp_user'
      AND (
          c.relname LIKE 'payroll_regulatory_%'
          OR c.relname LIKE 'payroll_statutory_%'
          OR c.relname LIKE 'payroll_tax_remittance%'
      );

    IF wrong_functions != 0 THEN
        RAISE EXCEPTION
            'Application still owns protected functions';
    END IF;

    IF pg_has_role(
        'erp_user', 'erp_snapshot_owner', 'MEMBER'
    ) THEN
        RAISE EXCEPTION
            'Application inherits protected owner role';
    END IF;

    IF NOT has_table_privilege(
        'erp_user',
        'public.payroll_statutory_result_lines',
        'INSERT'
    ) THEN
        RAISE EXCEPTION
            'Application lost payroll INSERT privilege';
    END IF;

    IF NOT has_table_privilege(
        'erp_user',
        'public.payroll_regulatory_report_rows',
        'UPDATE'
    ) THEN
        RAISE EXCEPTION
            'Application lost regulatory UPDATE privilege';
    END IF;
END;
$security$;

SET LOCAL ROLE erp_user;

DO $negative$
BEGIN
    BEGIN
        EXECUTE
            'ALTER TABLE public.payroll_statutory_result_lines '
            'DISABLE TRIGGER trg_dated_statutory_line_immutable';

        RAISE EXCEPTION
            'SECURITY FAILURE: application disabled trigger';
    EXCEPTION
        WHEN insufficient_privilege THEN
            NULL;
    END;

    BEGIN
        EXECUTE
            'DROP TRIGGER trg_dated_statutory_line_immutable '
            'ON public.payroll_statutory_result_lines';

        RAISE EXCEPTION
            'SECURITY FAILURE: application dropped trigger';
    EXCEPTION
        WHEN insufficient_privilege THEN
            NULL;
    END;
END;
$negative$;

RESET ROLE;

DO $trigger$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.oid =
            'public.payroll_statutory_result_lines'::regclass
          AND t.tgname =
              'trg_dated_statutory_line_immutable'
          AND t.tgenabled = 'O'
    ) THEN
        RAISE EXCEPTION
            'Protected statutory trigger not enabled';
    END IF;
END;
$trigger$;

ROLLBACK;

SELECT 'ROLE SEPARATION NEGATIVE E2E = PASS';
""",
    )

    assert (
        "ROLE SEPARATION NEGATIVE E2E = PASS"
        in checks
    )

    print(
        "ISOLATED OWNER TRANSFER = PASS",
        "APPLICATION DML PRIVILEGES = PASS",
        "APPLICATION DISABLE TRIGGER = BLOCKED",
        "APPLICATION DROP TRIGGER = BLOCKED",
        "PROTECTED TRIGGER STILL ENABLED = PASS",
        "ROLE SEPARATION NEGATIVE E2E = PASS",
        flush=True,
        sep="\n",
    )
