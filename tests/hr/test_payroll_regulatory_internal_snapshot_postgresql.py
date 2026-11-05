"""Actual PostgreSQL DDL and immutable snapshot acceptance.

Runs only in the disposable upgraded database.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
)

from app.core.config import settings


@pytest.mark.asyncio
async def test_real_regulatory_snapshot_schema_and_guards():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    engine = create_async_engine(settings.database_url)

    try:
        sessions = async_sessionmaker(
            engine, expire_on_commit=False
        )

        async with sessions() as db:
            constraints = (
                await db.execute(
                    text("""
                        SELECT conname
                        FROM pg_constraint
                        WHERE conrelid =
                            'public.payroll_regulatory_internal_snapshots'
                            ::regclass
                    """)
                )
            ).scalars().all()

            assert "fk_regint_period" in constraints
            assert "fk_regint_d1" in constraints
            assert "fk_regint_4df" in constraints
            assert "fk_regint_d5" in constraints
            assert "fk_regint_d6" in constraints
            assert "fk_regint_previous" in constraints
            assert "ck_regint_status" in constraints
            assert "ck_regint_digest" in constraints

            triggers = (
                await db.execute(
                    text("""
                        SELECT tgname, tgenabled
                        FROM pg_trigger
                        WHERE tgrelid =
                            'public.payroll_regulatory_internal_snapshots'
                            ::regclass
                          AND NOT tgisinternal
                    """)
                )
            ).all()

            active = {
                name
                for name, enabled in triggers
                if enabled in ("O", b"O")
            }

            assert (
                "trg_regulatory_internal_snapshot_immutable"
                in active
            )
            assert (
                "trg_regulatory_internal_snapshot_no_truncate"
                in active
            )

    finally:
        await engine.dispose()
