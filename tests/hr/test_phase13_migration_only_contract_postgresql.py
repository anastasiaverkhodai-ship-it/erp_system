import os
import re

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

pytestmark = pytest.mark.asyncio

TABLES = {
    "payroll_payment_tax_allocation_candidates",
    "payroll_payment_tax_allocation_candidate_events",
    "payroll_payment_tax_allocation_corrections",
    "payroll_payment_tax_evidence",
    "payroll_payment_tax_document_integrity",
    "payroll_payment_tax_document_reviews",
    "payroll_payment_tax_secure_custody",
    "payroll_external_receipt_registry",
}


async def test_migration_only_tables_are_preserved():
    assert os.getenv("RUN_POSTGRES_E2E") == "1"

    url = make_url(str(settings.database_url))
    assert re.fullmatch(
        r"erp_r3_copy_[0-9a-f]{16}",
        url.database or "",
    )
    assert url.username == "erp_user"
    assert url.host in (
        "localhost", "127.0.0.1", "::1"
    )

    engine = create_async_engine(url)

    try:
        async with engine.connect() as db:
            records = (
                await db.execute(text("""
                    SELECT c.relname
                    FROM pg_class c
                    JOIN pg_namespace n
                      ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind IN ('r', 'p')
                """))
            ).scalars().all()

            assert TABLES <= set(records)

            active = (
                await db.execute(text("""
                    SELECT c.relname
                    FROM pg_trigger t
                    JOIN pg_class c
                      ON c.oid = t.tgrelid
                    JOIN pg_namespace n
                      ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND NOT t.tgisinternal
                      AND t.tgenabled IN ('O', 'A')
                """))
            ).scalars().all()

            protected = {
                "payroll_payment_tax_allocation_candidates",
                "payroll_payment_tax_allocation_corrections",
                "payroll_payment_tax_secure_custody",
                "payroll_external_receipt_registry",
            }

            assert protected <= set(active)

            owner = await db.scalar(text("""
                SELECT pg_get_userbyid(c.relowner)
                FROM pg_class c
                JOIN pg_namespace n
                  ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname =
                    'payroll_external_receipt_registry'
            """))

            assert owner == "erp_snapshot_owner"

    finally:
        await engine.dispose()
