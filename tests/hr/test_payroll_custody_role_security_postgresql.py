"""Real PostgreSQL role proof in a separately owned disposable DB."""

import importlib.util
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

import app.models
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.database import Base, engine as application_engine
from app.services.payroll_custody_role_security import (
    TABLES,
    audit_custody_runtime_connection,
)

pytestmark = pytest.mark.asyncio

BASE_REVISION = "p13d6grant261105"
TARGET_REVISION = "p13custrole261108"


def required_chain():
    scripts = ScriptDirectory.from_config(
        Config("alembic.ini")
    )

    revisions = list(scripts.iterate_revisions(
        TARGET_REVISION, BASE_REVISION
    ))
    revisions.reverse()

    expected = [
        "p13wtalloc261107",
        "p13wtrev261107",
        "p13wtcorr261107",
        "p13wtevid261107",
        "p13wtint261107",
        "p13wtcust261107",
        "p13custrole261108",
    ]

    assert [revision.revision for revision in revisions] == expected
    return revisions


async def database_command(engine, statement):
    async with engine.connect() as connection:
        await connection.execute(text(statement))


@pytest_asyncio.fixture
async def custody_engine():
    """One independently owned temporary DB per test.

    It is neither the production DB nor an employee schema.
    The administrator applies migrations; erp_user tests access.
    """
    source_url = application_engine.url

    if (
        source_url.username != "erp_user"
        or source_url.database is None
        or not (
            source_url.database == "erp_db"
            or re.fullmatch(
                r"erp_r3_copy_[0-9a-f]{16}",
                source_url.database
            )
        )
    ):
        raise AssertionError(
            "STOP: Unexpected application database configuration"
        )

    admin_user = os.environ["ERP_TEST_ADMIN_USER"]
    socket = os.environ["ERP_TEST_ADMIN_SOCKET"]

    database = "erp_custody_r5_" + uuid4().hex[:20]
    assert re.fullmatch(
        r"erp_custody_r5_[0-9a-f]{20}", database
    )

    maintenance_url = URL.create(
        "postgresql+asyncpg",
        username=admin_user,
        database="postgres",
        query={"host": socket},
    )

    migration_url = maintenance_url.set(database=database)
    runtime_url = source_url.set(database=database)

    maintenance = create_async_engine(
        maintenance_url,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )

    migration_engine = None
    runtime_engine = None
    created = False

    try:
        async with maintenance.connect() as connection:
            superuser = await connection.scalar(
                text("""
                    SELECT rolsuper
                    FROM pg_roles
                    WHERE rolname=current_user
                """)
            )
            assert superuser is True

            await connection.execute(
                text(
                    f'CREATE DATABASE "{database}" '
                    f'OWNER "{admin_user}" TEMPLATE template0'
                )
            )
            created = True

            await connection.execute(
                text(
                    f'GRANT CONNECT ON DATABASE "{database}" '
                    'TO erp_user'
                )
            )

        migration_engine = create_async_engine(
            migration_url,
            poolclass=NullPool,
        )
        runtime_engine = create_async_engine(
            runtime_url,
            poolclass=NullPool,
        )

        async with migration_engine.begin() as connection:
            identity = (
                await connection.execute(
                    text("""
                        SELECT current_database() AS db,
                               current_user AS role,
                               pg_get_userbyid(d.datdba) AS owner
                        FROM pg_database d
                        WHERE d.datname=current_database()
                    """)
                )
            ).mappings().one()

            assert identity["db"] == database
            assert identity["role"] == admin_user
            assert identity["owner"] == admin_user

            await connection.run_sync(Base.metadata.create_all)

            for revision in required_chain():
                path = Path(revision.path)
                spec = importlib.util.spec_from_file_location(
                    "disposable_" + revision.revision, path
                )
                assert spec is not None
                assert spec.loader is not None

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                assert module.revision == revision.revision

                def upgrade(sync_connection):
                    context = MigrationContext.configure(
                        sync_connection
                    )
                    with Operations.context(context):
                        module.upgrade()

                await connection.run_sync(upgrade)

            # Business-operation E2E runs under erp_user.
            # All database objects here belong to the disposable
            # test DB; never grant mutable privileges to custody.
            await connection.execute(text("""
                DO $$
                DECLARE
                    item record;
                BEGIN
                    FOR item IN
                        SELECT n.nspname AS schema_name,
                               c.relname AS table_name
                        FROM pg_class c
                        JOIN pg_namespace n
                          ON n.oid=c.relnamespace
                        WHERE n.nspname='public'
                          AND c.relkind IN ('r','p')
                          AND c.relname NOT IN (
                            'payroll_payment_tax_secure_custody',
                            'payroll_payment_tax_document_integrity',
                            'payroll_payment_tax_document_reviews'
                          )
                    LOOP
                        EXECUTE format(
                          'GRANT SELECT, INSERT, UPDATE, DELETE '
                          'ON TABLE %I.%I TO erp_user',
                          item.schema_name,
                          item.table_name
                        );
                    END LOOP;
                END
                $$;
            """))

            await connection.execute(text("""
                GRANT USAGE, SELECT
                ON ALL SEQUENCES IN SCHEMA public
                TO erp_user
            """))

            await connection.execute(
                text("GRANT USAGE ON SCHEMA public TO erp_user")
            )
            await connection.execute(
                text(
                    "REVOKE CREATE ON SCHEMA public "
                    "FROM PUBLIC, erp_user"
                )
            )

            count = await connection.scalar(
                text("""
                    SELECT count(*)
                    FROM pg_trigger
                    WHERE tgname='trg_p13_custody_immutable'
                      AND tgenabled IN ('O','A')
                      AND tgrelid IN (
                        to_regclass(
                          'public.payroll_payment_tax_secure_custody'
                        ),
                        to_regclass(
                          'public.payroll_payment_tax_document_integrity'
                        ),
                        to_regclass(
                          'public.payroll_payment_tax_document_reviews'
                        )
                      )
                """)
            )
            assert count == 3

        async with runtime_engine.connect() as connection:
            role = await connection.scalar(
                text("SELECT current_user")
            )
            dbname = await connection.scalar(
                text("SELECT current_database()")
            )
            db_create = await connection.scalar(
                text("""
                    SELECT has_database_privilege(
                        current_user,
                        current_database(),
                        'CREATE'
                    )
                """)
            )
            schema_create = await connection.scalar(
                text("""
                    SELECT has_schema_privilege(
                        current_user, 'public', 'CREATE'
                    )
                """)
            )

            assert role == "erp_user"
            assert dbname == database
            assert db_create is False
            assert schema_create is False

        yield runtime_engine

    finally:
        if runtime_engine is not None:
            await runtime_engine.dispose()

        if migration_engine is not None:
            await migration_engine.dispose()

        try:
            if created:
                await database_command(
                    maintenance,
                    f'DROP DATABASE "{database}" WITH (FORCE)',
                )
        finally:
            await maintenance.dispose()


@pytest.mark.parametrize("table", TABLES)
async def test_custody_role_privileges(custody_engine, table):
    async with AsyncSession(
        custody_engine,
        expire_on_commit=False,
    ) as db:
        result = await audit_custody_runtime_connection(db)

        assert result["security_status"] == (
            "RESTRICTED_CUSTODY_ROLE_VERIFIED"
        ), result["failures"]
        assert result["table_count"] == 3

        row = (
            await db.execute(
                text("""
                    SELECT
                        current_user AS role,
                        has_table_privilege(
                            :table,'SELECT'
                        ) AS can_select,
                        has_table_privilege(
                            :table,'INSERT'
                        ) AS can_insert,
                        has_table_privilege(
                            :table,'UPDATE'
                        ) AS can_update,
                        has_table_privilege(
                            :table,'DELETE'
                        ) AS can_delete,
                        has_table_privilege(
                            :table,'TRUNCATE'
                        ) AS can_truncate
                """),
                {"table": "public." + table},
            )
        ).mappings().one()

        assert row["role"] == "erp_user"
        assert row["can_select"] is True
        assert row["can_insert"] is True
        assert row["can_update"] is False
        assert row["can_delete"] is False
        assert row["can_truncate"] is False

        await db.rollback()


@pytest.mark.parametrize("table", TABLES)
async def test_custody_trigger_disable_denied(
    custody_engine, table
):
    assert table in TABLES

    async with AsyncSession(
        custody_engine,
        expire_on_commit=False,
    ) as db:
        # Start a transaction before the nested savepoint.
        await db.execute(text("SELECT current_user"))

        savepoint = await db.begin_nested()

        with pytest.raises(DBAPIError):
            await db.execute(
                text(
                    f"ALTER TABLE public.{table} "
                    "DISABLE TRIGGER trg_p13_custody_immutable"
                )
            )

        await savepoint.rollback()

        state = await db.scalar(
            text("""
                SELECT tgenabled
                FROM pg_trigger
                WHERE tgrelid=to_regclass(:table)
                  AND tgname='trg_p13_custody_immutable'
            """),
            {"table": "public." + table},
        )
        if isinstance(state, bytes):
            state = state.decode("ascii")
        assert state in ("O", "A")

        await db.rollback()
