"""Real PostgreSQL contracts for the severance snapshot schema.

Requires the isolated regression-copy runner with --upgrade-copy.
No test may execute against the primary ERP database.
"""

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine


TABLE = "payroll_severance_statutory_snapshots"

EXPECTED_CHECKS = {
    "ck_psss_version",
    "ck_psss_currency",
    "ck_psss_gross",
    "ck_psss_bases",
    "ck_psss_policy",
    "ck_psss_dates",
    "ck_psss_fingerprint",
    "ck_psss_sources",
}

EXPECTED_UNIQUES = {
    "uq_psss_calculation",
    "uq_psss_company_id",
}


def _pg_char(value):
    """Normalize PostgreSQL internal char returned as str or bytes."""
    if isinstance(value, bytes):
        value = value.decode("ascii")
    assert isinstance(value, str) and len(value) == 1, (
        "Unexpected PostgreSQL catalog char",
        repr(value),
    )
    return value


async def _run_on_isolated_db(callback):
    raw_url = os.environ.get("DATABASE_URL")
    assert raw_url, "DATABASE_URL must be provided by isolated runner"

    url = make_url(raw_url)
    database_name = url.database or ""

    # Never permit these tests to target the original ERP database.
    assert database_name != "erp_db"
    assert "copy" in database_name.lower() or (
        "test" in database_name.lower()
    ), (
        "Refusing unexpected database name",
        database_name,
    )

    url = url.set(drivername="postgresql+asyncpg")
    engine = create_async_engine(url)

    try:
        async with engine.connect() as conn:
            version = (
                await conn.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            ).scalar_one()
            assert version == "13d4a7b8c063"

            return await callback(conn)
    finally:
        await engine.dispose()


def _execute(callback):
    return asyncio.run(_run_on_isolated_db(callback))


def test_postgresql_constraints_are_active_and_validated():
    async def check(conn):
        rows = (
            await conn.execute(
                text("""
                    SELECT conname, contype, convalidated
                    FROM pg_constraint
                    WHERE conrelid = to_regclass(
                        'public.payroll_severance_statutory_snapshots'
                    )
                """)
            )
        ).all()

        assert rows, "Snapshot table missing"

        checks = {
            row.conname
            for row in rows
            if _pg_char(row.contype) == "c" and row.convalidated
        }
        uniques = {
            row.conname
            for row in rows
            if _pg_char(row.contype) == "u" and row.convalidated
        }

        assert EXPECTED_CHECKS <= checks, (
            EXPECTED_CHECKS - checks
        )
        assert EXPECTED_UNIQUES <= uniques, (
            EXPECTED_UNIQUES - uniques
        )

        fks = [
            row for row in rows
            if _pg_char(row.contype) == "f" and row.convalidated
        ]
        assert any(
            row.conname == "fk_psss_calculation"
            for row in fks
        )

    _execute(check)


def test_postgresql_source_column_is_jsonb():
    async def check(conn):
        data_type = (
            await conn.execute(
                text("""
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name =
                        'payroll_severance_statutory_snapshots'
                      AND column_name = 'severance_source_ids'
                """)
            )
        ).scalar_one()

        assert data_type == "jsonb"

    _execute(check)


def test_postgresql_immutability_triggers_are_enabled():
    async def check(conn):
        rows = (
            await conn.execute(
                text("""
                    SELECT tgname, tgenabled,
                           pg_get_triggerdef(oid) AS definition
                    FROM pg_trigger
                    WHERE tgrelid = to_regclass(
                        'public.payroll_severance_statutory_snapshots'
                    )
                    AND NOT tgisinternal
                """)
            )
        ).all()

        by_name = {row.tgname: row for row in rows}

        assert {
            "trg_psss_validate",
            "trg_psss_no_truncate",
        } <= set(by_name)

        validate = by_name["trg_psss_validate"]
        truncate = by_name["trg_psss_no_truncate"]

        assert _pg_char(validate.tgenabled) == "O"
        assert _pg_char(truncate.tgenabled) == "O"

        definition = validate.definition.upper()
        assert "BEFORE" in definition
        for operation in ("INSERT", "UPDATE", "DELETE"):
            assert operation in definition

        assert "TRUNCATE" in truncate.definition.upper()

    _execute(check)


def test_postgresql_immutable_function_rejects_mutations():
    async def check(conn):
        definition = (
            await conn.execute(
                text("""
                    SELECT pg_get_functiondef(
                        'payroll_severance_snapshot_validate()'::regprocedure
                    )
                """)
            )
        ).scalar_one()

        normalized = " ".join(definition.split()).lower()

        assert "tg_op <> 'insert'" in normalized
        assert "historical severance snapshot is immutable" in normalized
        assert "payroll_calculation_lines" in normalized
        assert "source_termination_severance_id" in normalized

    _execute(check)


def test_postgresql_truncate_is_actually_rejected():
    async def check(conn):
        # The revision SELECT in _run_on_isolated_db already
        # starts a SQLAlchemy transaction through autobegin.
        assert conn.in_transaction()
        transaction = conn.get_transaction()
        assert transaction is not None

        try:
            nested = await conn.begin_nested()

            try:
                with pytest.raises(DBAPIError) as error:
                    await conn.execute(
                        text(
                            "TRUNCATE TABLE "
                            "payroll_severance_statutory_snapshots"
                        )
                    )

                assert (
                    "historical severance snapshot cannot be truncated"
                    in str(error.value).lower()
                )
            finally:
                if nested.is_active:
                    await nested.rollback()
        finally:
            if transaction.is_active:
                await transaction.rollback()

    _execute(check)
