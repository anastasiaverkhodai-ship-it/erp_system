"""Verify immutable payroll snapshot protection as the actual ERP runtime.

Usage:
    python -m scripts.verify_payroll_snapshot_runtime_security

This tool:
- connects only to explicitly verified erp_db;
- requires the real erp_user login;
- performs SQL probes inside rollback-only transactions;
- requires PostgreSQL insufficient_privilege (42501) on forbidden SQL;
- never migrates the database or changes ownership.

Intended for controlled acceptance after DBA role remediation.
"""

import asyncio
import os

from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

TABLE = "public.payroll_regulatory_correction_snapshots"
EXPECTED_REVISION = "p13rpt261102a"
PRIVILEGE_DENIED = "42501"


def sqlstate(error):
    """Extract the actual PostgreSQL SQLSTATE from SQLAlchemy's DBAPI error."""
    candidate = getattr(error, "orig", error)
    visited = set()

    for _ in range(8):
        if candidate is None or id(candidate) in visited:
            break
        visited.add(id(candidate))

        for attr in ("sqlstate", "pgcode"):
            value = getattr(candidate, attr, None)
            if value:
                return value

        candidate = (
            getattr(candidate, "__cause__", None)
            or getattr(candidate, "__context__", None)
        )

    return None


def validate_runtime_identity(
    database, current_user, session_user, owner, privileges,
    is_superuser, can_assume_owner,
):
    if (database, current_user, session_user) != (
        "erp_db", "erp_user", "erp_user"
    ):
        raise AssertionError("Wrong runtime connection identity")

    if owner != "erp_snapshot_owner":
        raise AssertionError("Snapshot owner separation missing")

    if list(privileges) != [True, True, False, False, False]:
        raise AssertionError("Unsafe effective runtime privileges")

    if is_superuser is not False or can_assume_owner is not False:
        raise AssertionError("Runtime can bypass snapshot protections")


async def forbidden_statement(connection, label, statement):
    savepoint = await connection.begin_nested()
    denied = False

    try:
        try:
            await connection.execute(text(statement))
        except DBAPIError as error:
            state = sqlstate(error)
            if state != PRIVILEGE_DENIED:
                raise AssertionError(
                    f"{label}: expected 42501, received {state}"
                ) from error
            denied = True
    finally:
        await savepoint.rollback()

    if not denied:
        raise AssertionError(f"CRITICAL: {label} was permitted")

    print(f"{label} = BLOCKED / PASS")


async def verify():
    raw = (
        os.environ.get("DATABASE_URL")
        or dotenv_values(".env").get("DATABASE_URL")
    )

    if not raw:
        raise RuntimeError("DATABASE_URL unavailable")

    url = make_url(raw)

    if url.database != "erp_db" or url.username != "erp_user":
        raise RuntimeError(
            "Refusing to run outside ERP runtime connection"
        )

    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("PostgreSQL URL required")

    if url.drivername != "postgresql+asyncpg":
        url = url.set(drivername="postgresql+asyncpg")

    engine = create_async_engine(url, pool_pre_ping=True)

    try:
        async with engine.connect() as conn:
            tx = await conn.begin()

            try:
                identity = (
                    await conn.execute(
                        text("""
                            SELECT
                                current_database(),
                                current_user,
                                session_user
                        """)
                    )
                ).one()

                revision = await conn.scalar(
                    text("""
                        SELECT version_num
                        FROM public.alembic_version
                    """)
                )

                if revision != EXPECTED_REVISION:
                    raise AssertionError(
                        f"Unexpected Alembic revision: {revision}"
                    )

                owner = await conn.scalar(
                    text("""
                        SELECT pg_get_userbyid(relowner)
                        FROM pg_class
                        WHERE oid = to_regclass(
                            'public.payroll_regulatory_correction_snapshots'
                        )
                    """)
                )

                privileges = (
                    await conn.execute(
                        text("""
                            SELECT
                                has_table_privilege(
                                    current_user, oid, 'SELECT'
                                ),
                                has_table_privilege(
                                    current_user, oid, 'INSERT'
                                ),
                                has_table_privilege(
                                    current_user, oid, 'UPDATE'
                                ),
                                has_table_privilege(
                                    current_user, oid, 'DELETE'
                                ),
                                has_table_privilege(
                                    current_user, oid, 'TRUNCATE'
                                )
                            FROM pg_class
                            WHERE oid = to_regclass(
                                'public.payroll_regulatory_correction_snapshots'
                            )
                        """)
                    )
                ).one()

                is_superuser = await conn.scalar(
                    text("""
                        SELECT rolsuper
                        FROM pg_roles
                        WHERE rolname = current_user
                    """)
                )

                can_assume_owner = await conn.scalar(
                    text("""
                        SELECT pg_has_role(
                            current_user,
                            'erp_snapshot_owner',
                            'MEMBER'
                        )
                    """)
                )

                validate_runtime_identity(
                    *identity,
                    owner,
                    privileges,
                    is_superuser,
                    can_assume_owner,
                )

                print("RUNTIME IDENTITY = PASS")
                print("OWNER SEPARATION = PASS")
                print("EFFECTIVE PRIVILEGES = PASS")

                trigger_count = await conn.scalar(
                    text("""
                        SELECT count(*)
                        FROM pg_trigger
                        WHERE tgrelid = to_regclass(
                            'public.payroll_regulatory_correction_snapshots'
                        )
                          AND tgname = 'trg_prcs_immutable'
                          AND tgenabled = 'O'
                          AND NOT tgisinternal
                    """)
                )

                if trigger_count != 1:
                    raise AssertionError("Immutable trigger not enabled")

                before = await conn.scalar(
                    text(f"SELECT count(*) FROM {TABLE}")
                )

                # Real privilege-checked INSERT, but SELECT ... WHERE FALSE
                # guarantees no row is created.
                await conn.execute(
                    text(f"""
                        INSERT INTO {TABLE} (
                            company_id,
                            payroll_period_id,
                            report_kind,
                            original_calculation_id,
                            replacement_calculation_id,
                            schema_version,
                            projection,
                            projection_sha256
                        )
                        SELECT
                            1, 1, 'd1', 1, 2,
                            'security_probe',
                            '{{}}'::jsonb,
                            repeat('0', 64)
                        WHERE FALSE
                    """)
                )

                print("SELECT + INSERT ACCESS = PASS")

                for label, statement in (
                    (
                        "UPDATE",
                        f"UPDATE {TABLE} "
                        "SET schema_version = schema_version "
                        "WHERE FALSE",
                    ),
                    (
                        "DELETE",
                        f"DELETE FROM {TABLE} WHERE FALSE",
                    ),
                    (
                        "TRUNCATE",
                        f"TRUNCATE TABLE {TABLE}",
                    ),
                    (
                        "DISABLE TRIGGER",
                        f"ALTER TABLE {TABLE} "
                        "DISABLE TRIGGER trg_prcs_immutable",
                    ),
                ):
                    await forbidden_statement(conn, label, statement)

                after = await conn.scalar(
                    text(f"SELECT count(*) FROM {TABLE}")
                )

                if before != after:
                    raise AssertionError("Snapshot row count changed")

                enabled_after = await conn.scalar(
                    text("""
                        SELECT count(*)
                        FROM pg_trigger
                        WHERE tgrelid = to_regclass(
                            'public.payroll_regulatory_correction_snapshots'
                        )
                          AND tgname = 'trg_prcs_immutable'
                          AND tgenabled = 'O'
                    """)
                )

                if enabled_after != 1:
                    raise AssertionError("Trigger disabled after probes")

                print("SNAPSHOT COUNT UNCHANGED = PASS")
                print("IMMUTABLE TRIGGER PRESERVED = PASS")
                print("REAL ERP RUNTIME SECURITY = PASS")

            finally:
                await tx.rollback()

    finally:
        await engine.dispose()


def main():
    asyncio.run(verify())


if __name__ == "__main__":
    main()
