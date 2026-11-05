"""Verify D6 runtime read access on a real isolated copy."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings


@pytest.mark.asyncio
async def test_d6_runtime_select_permissions():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    url = make_url(settings.database_url)

    assert url.database
    assert url.database.startswith("erp_r3_copy_")
    assert url.database != "erp_db"
    assert url.username == "erp_user"

    engine = create_async_engine(url)

    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("""
                        SELECT
                            current_database(),
                            current_user,
                            (
                                SELECT version_num
                                FROM public.alembic_version
                            ),
                            has_table_privilege(
                                current_user,
                                'public.payroll_d6_evidence',
                                'SELECT'
                            ),
                            has_table_privilege(
                                current_user,
                                'public.payroll_d6_evidence_reviews',
                                'SELECT'
                            ),
                            pg_has_role(
                                current_user,
                                'erp_snapshot_owner',
                                'MEMBER'
                            )
                    """)
                )
            ).one()

            assert row[0] == url.database
            assert row[1] == "erp_user"
            assert row[2] == "p13d6grant261105"
            assert row[3] is True
            assert row[4] is True
            assert row[5] is False

            # Real SELECTs, rather than only catalog checks.
            await conn.execute(
                text("""
                    SELECT id
                    FROM public.payroll_d6_evidence
                    LIMIT 1
                """)
            )

            await conn.execute(
                text("""
                    SELECT id
                    FROM public.payroll_d6_evidence_reviews
                    LIMIT 1
                """)
            )

            await conn.rollback()
    finally:
        await engine.dispose()
