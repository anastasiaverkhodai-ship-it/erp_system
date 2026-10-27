"""PostgreSQL-only migration contract for advance tax remittances."""

import os

import pytest
from sqlalchemy import text
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.models.payroll_advance_tax_remittance import (
    PayrollAdvanceTaxRemittance,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="PostgreSQL required",
    ),
]


async def test_advance_tax_remittance_real_migration(migrated_copy_engine):
    async with migrated_copy_engine.connect() as db:
        revision = (
            await db.execute(
                text("SELECT version_num FROM alembic_version")
            )
        ).scalar_one()

        assert revision == ScriptDirectory.from_config(Config("alembic.ini")).get_heads()[0]

        table_name = PayrollAdvanceTaxRemittance.__tablename__
        assert table_name == "payroll_advance_tax_remittances"

        table_exists = (
            await db.execute(
                text("SELECT to_regclass(:table_name)"),
                {"table_name": table_name},
            )
        ).scalar_one()

        assert table_exists is not None

        columns = (
            await db.execute(
                text("""
                    SELECT column_name, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = :table_name
                """),
                {"table_name": table_name},
            )
        ).all()

        actual_columns = {
            name: nullable for name, nullable in columns
        }

        expected_columns = {
            column.name
            for column in PayrollAdvanceTaxRemittance.__table__.columns
        }

        assert set(actual_columns) == expected_columns

        for name in (
            "company_id",
            "payroll_advance_id",
            "journal_entry_id",
            "bank_account_id",
            "component",
            "amount",
            "request_key",
            "request_fingerprint",
            "document_reference",
            "budget_payment_reference",
            "created_by",
        ):
            assert actual_columns[name] == "NO", name

        rows = (
            await db.execute(
                text("""
                    SELECT conname, contype,
                           pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conrelid = to_regclass(:table_name)
                """),
                {"table_name": table_name},
            )
        ).all()

        constraints = {
            name: (kind, definition)
            for name, kind, definition in rows
        }

        expected_constraints = {
            "uq_advance_tax_remittance_company_id": "u",
            "uq_advance_tax_remittance_request": "u",
            "uq_advance_tax_remittance_journal": "u",
            "uq_advance_tax_remittance_component": "u",
            "fk_advance_tax_remittance_advance": "f",
            "fk_advance_tax_remittance_journal": "f",
            "fk_advance_tax_remittance_bank": "f",
            "ck_advance_tax_remittance_component": "c",
            "ck_advance_tax_remittance_positive": "c",
        }

        for name, kind in expected_constraints.items():
            assert name in constraints, name
            assert (constraints[name][0].decode('ascii') if isinstance(constraints[name][0], bytes) else constraints[name][0]) == kind, name

        amount_check = constraints[
            "ck_advance_tax_remittance_positive"
        ][1]
        component_check = constraints[
            "ck_advance_tax_remittance_component"
        ][1]

        assert "amount >" in amount_check
        assert "personal_income_tax" in component_check
        assert "military_levy" in component_check

        row_count = (
            await db.execute(
                text(
                    "SELECT count(*) FROM "
                    "payroll_advance_tax_remittances"
                )
            )
        ).scalar_one()

        assert row_count == 0

    print("R6-B2-A REAL MIGRATION REVISION = PASS")
    print("R6-B2-A TABLE AND COLUMNS = PASS")
    print("R6-B2-A COMPOSITE FOREIGN KEYS = PASS")
    print("R6-B2-A UNIQUENESS = PASS")
    print("R6-B2-A CHECK CONSTRAINTS = PASS")
    print("R6-B2-A NEW TABLE INITIAL ROWS = ZERO")


import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings


@pytest_asyncio.fixture
async def migrated_copy_engine():
    if os.getenv("RUN_POSTGRES_E2E") != "1":
        raise RuntimeError("PostgreSQL E2E mode required")

    url = make_url(settings.database_url)

    if (
        url.get_backend_name() != "postgresql"
        or url.host not in ("localhost", "127.0.0.1", "::1")
        or not (url.database or "").startswith("erp_r3_copy_")
    ):
        raise RuntimeError(
            "STOP: Refusing non-isolated PostgreSQL target"
        )

    engine = create_async_engine(url, poolclass=NullPool)

    try:
        async with engine.connect() as connection:
            database_name = (
                await connection.execute(
                    text("SELECT current_database()")
                )
            ).scalar_one()

            assert database_name == url.database

            revision = (
                await connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            ).scalar_one()

            assert revision == ScriptDirectory.from_config(Config("alembic.ini")).get_heads()[0]

            await connection.rollback()

        print("R6-B2-A ISOLATED DATABASE TARGET = VERIFIED")

        yield engine

    finally:
        await engine.dispose()
