"""Run PostgreSQL regression against a disposable full PostgreSQL clone.

Usage:
    PYTHONPATH=. .venv/bin/python \\
        scripts/run_postgresql_regression_copy.py \\
        tests/hr/test_payroll_deduction_remittance_persistence_postgresql.py

The source erp_db is never used as the pytest target.
pg_dump reads the source; pg_restore builds a separate database,
preserving migration-created functions, indexes and triggers.

The temporary dump and database can contain real data. They are
private and removed during normal completion and handled failures.
Pass --upgrade-copy to upgrade only the disposable copy to the code head
and verify schema alignment before pytest. The source revision stays unchanged.
Requires local PostgreSQL and CREATE DATABASE permission.
"""

import asyncio
import os
import re
import subprocess
import sys
import tempfile
import uuid

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings


PREFIX = "erp_r3_copy_"
NAME_PATTERN = re.compile(r"erp_r3_copy_[0-9a-f]{16}")

REQUIRED_TRIGGERS = {
    "payroll_deduction_remainders": "trg_pdr_no_truncate",
    "payroll_deduction_remainder_events": "trg_pdre_no_truncate",
    "payroll_deduction_remittance_plans": "trg_pdrp_no_truncate",
    "payroll_deduction_remittance_events": "trg_pdrevent_no_truncate",
}


def require(condition, message):
    if not condition:
        raise RuntimeError("STOP: " + message)



async def remove_isolated_database(conn, database_name):
    require(NAME_PATTERN.fullmatch(database_name) is not None,
            "Unsafe temporary database cleanup")
    # Ordinary DROP handles autovacuum workers internally. FORCE can instead
    # require pg_signal_backend for those server-owned workers.
    try:
        await conn.execute(text(f'DROP DATABASE "{database_name}"'))
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "55006":
            raise
        # Remaining client sessions may belong to this interrupted test run.
        await conn.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))

def pg_environment(url):
    env = dict(os.environ)
    env["PGHOST"] = url.host
    env["PGPORT"] = str(url.port or 5432)
    env["PGUSER"] = url.username or ""
    env["PGDATABASE"] = url.database

    if url.password is not None:
        env["PGPASSWORD"] = url.password

    return env


def pg_command(args, env):
    result = subprocess.run(
        args,
        env=env,
        capture_output=True,
        text=True,
    )

    if result.returncode:
        # Do not expose connection URLs or credentials.
        print(
            "POSTGRESQL CLIENT COMMAND FAILED:",
            args[0],
            "EXIT:",
            result.returncode,
            flush=True,
        )
        raise RuntimeError("PostgreSQL dump/restore failed")


async def revision(engine):
    async with engine.connect() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        return await conn.scalar(
            text("SELECT version_num FROM alembic_version")
        )


async def trigger_contract(engine):
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("""
                    SELECT
                        c.relname AS table_name,
                        t.tgname AS trigger_name,
                        t.tgenabled AS enabled,
                        pg_get_triggerdef(t.oid) AS definition
                    FROM pg_trigger t
                    JOIN pg_class c ON c.oid = t.tgrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND NOT t.tgisinternal
                      AND c.relname = ANY(:tables)
                """),
                {"tables": list(REQUIRED_TRIGGERS)},
            )
        ).mappings().all()

    for table, expected in REQUIRED_TRIGGERS.items():
        matches = [
            row for row in rows
            if row["table_name"] == table
            and row["trigger_name"] == expected
        ]

        require(
            len(matches) == 1,
            f"Required PostgreSQL trigger missing: {table}",
        )

        row = matches[0]
        enabled = row["enabled"]

        if isinstance(enabled, bytes):
            enabled = enabled.decode()

        require(
            enabled in ("O", "A")
            and "BEFORE TRUNCATE" in row["definition"],
            f"Required PostgreSQL trigger invalid: {table}",
        )


async def run_copy():
    upgrade_copy = "--upgrade-copy" in sys.argv[1:]
    test_paths = [arg for arg in sys.argv[1:] if arg != "--upgrade-copy"]
    require(
        bool(test_paths),
        "Provide at least one pytest test path",
    )

    source_url = make_url(settings.database_url)

    require(
        source_url.get_backend_name() == "postgresql",
        "PostgreSQL is required",
    )

    require(
        source_url.host in ("localhost", "127.0.0.1", "::1"),
        "Only local PostgreSQL is allowed",
    )

    require(
        source_url.database == "erp_db",
        "Configured source database must be erp_db",
    )

    database_name = PREFIX + uuid.uuid4().hex[:16]

    require(
        NAME_PATTERN.fullmatch(database_name) is not None,
        "Unsafe temporary database name",
    )

    target_url = source_url.set(database=database_name)

    source = create_async_engine(
        source_url,
        poolclass=NullPool,
    )

    target = create_async_engine(
        target_url,
        poolclass=NullPool,
    )

    admin = create_async_engine(
        source_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )

    created = False

    try:
        # Coordinate complete-copy regressions to avoid excessive
        # concurrent PostgreSQL locks and transient test databases.
        async with admin.connect() as coordinator:
            acquired = await coordinator.scalar(
                text("SELECT pg_try_advisory_lock(130015, 1)")
            )

            if not acquired:
                print(
                    "WAITING FOR ANOTHER POSTGRESQL REGRESSION",
                    flush=True,
                )
                await coordinator.execute(
                    text("SELECT pg_advisory_lock(130015, 1)")
                )

            try:
                async with source.connect() as conn:
                    await conn.execute(
                        text("SET TRANSACTION READ ONLY")
                    )

                    current_database = await conn.scalar(
                        text("SELECT current_database()")
                    )

                    source_rev = await conn.scalar(
                        text(
                            "SELECT version_num FROM alembic_version"
                        )
                    )

                    await conn.rollback()

                require(
                    current_database == "erp_db",
                    "Unexpected source database",
                )

                print("SOURCE ALEMBIC REVISION =", source_rev, flush=True)
                await trigger_contract(source)

                with tempfile.TemporaryDirectory(
                    prefix="erp_r3_private_dump_"
                ) as temporary_directory:
                    dump_path = (
                        Path(temporary_directory) / "source.dump"
                    )

                    os.chmod(temporary_directory, 0o700)

                    source_env = pg_environment(source_url)

                    print(
                        "POSTGRESQL FULL DUMP START",
                        flush=True,
                    )

                    pg_command(
                        [
                            "pg_dump",
                            "--format=custom",
                            "--no-owner",
                            "--no-acl",
                            "--no-password",
                            "--file",
                            str(dump_path),
                            "erp_db",
                        ],
                        source_env,
                    )

                    require(
                        dump_path.is_file()
                        and dump_path.stat().st_size > 0,
                        "Dump file missing or empty",
                    )

                    os.chmod(dump_path, 0o600)

                    archive_check = subprocess.run(
                        ["pg_restore", "--list", str(dump_path)],
                        capture_output=True,
                        text=True,
                    )

                    require(
                        archive_check.returncode == 0
                        and "TABLE DATA" in archive_check.stdout,
                        "Invalid PostgreSQL archive",
                    )

                    print("POSTGRESQL FULL DUMP = PASS", flush=True)

                    async with admin.connect() as conn:
                        await conn.execute(
                            text(
                                f'CREATE DATABASE "{database_name}" '
                                "TEMPLATE template0"
                            )
                        )

                        created = True

                    print(
                        "ISOLATED DATABASE CREATED =",
                        database_name,
                        flush=True,
                    )

                    target_env = pg_environment(target_url)

                    pg_command(
                        [
                            "pg_restore",
                            "--exit-on-error",
                            "--no-owner",
                            "--no-acl",
                            "--no-password",
                            "--dbname",
                            database_name,
                            str(dump_path),
                        ],
                        target_env,
                    )

                    print(
                        "POSTGRESQL FULL RESTORE = PASS",
                        flush=True,
                    )

                    require(
                        await revision(target) == source_rev,
                        "Alembic revision mismatch",
                    )

                    await trigger_contract(target)

                    print(
                        "POSTGRESQL TRIGGER CONTRACT = PASS",
                        flush=True,
                    )

                    test_env = dict(os.environ)

                    test_env["DATABASE_URL"] = (
                        target_url.render_as_string(
                            hide_password=False
                        )
                    )

                    test_env["RUN_POSTGRES_E2E"] = "1"
                    test_env["PYTHONDONTWRITEBYTECODE"] = "1"

                    settings_check = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            (
                                "from app.core.config import settings; "
                                "from sqlalchemy.engine import make_url; "
                                "print(make_url(settings.database_url).database)"
                            ),
                        ],
                        env=test_env,
                        capture_output=True,
                        text=True,
                    )

                    require(
                        settings_check.returncode == 0
                        and settings_check.stdout.strip()
                        == database_name,
                        "Pytest database isolation override failed",
                    )

                    print(
                        "PYTEST DATABASE ISOLATION = PASS",
                        flush=True,
                    )

                    if upgrade_copy:
                        for operation in (("upgrade", "head"), ("check",)):
                            migration = await asyncio.create_subprocess_exec(
                                sys.executable, "-m", "alembic", *operation,
                                env=test_env,
                            )
                            require(await migration.wait() == 0, "Copy migration failed")

                    result = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "pytest",
                        *test_paths,
                        "-q",
                        "-p",
                        "no:cacheprovider",
                        env=test_env,
                    )

                    exit_code = await result.wait()

                    print(
                        "ISOLATED PYTEST EXIT =",
                        exit_code,
                        flush=True,
                    )

                    require(
                        await revision(source) == source_rev,
                        "Source Alembic revision changed during regression",
                    )
                    print("SOURCE ALEMBIC REVISION UNCHANGED = PASS", flush=True)

                    return exit_code

            finally:
                # Explicitly release the advisory lock even if
                # dump, restore, or pytest fails.
                await coordinator.execute(
                    text("SELECT pg_advisory_unlock(130015, 1)")
                )

    finally:
        await target.dispose()
        await source.dispose()

        try:
            if created:
                require(
                    NAME_PATTERN.fullmatch(database_name)
                    is not None,
                    "Unsafe temporary database cleanup",
                )

                async with admin.connect() as conn:
                    await remove_isolated_database(conn, database_name)

                print(
                    "ISOLATED DATABASE REMOVED = PASS",
                    flush=True,
                )

        finally:
            await admin.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(run_copy()))
