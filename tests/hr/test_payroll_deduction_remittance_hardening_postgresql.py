"""Phase 13 C5-B: real PostgreSQL transaction locks.

Uses transaction-scoped advisory locks only.
No inserts, business data updates or schema changes.
"""

import asyncio
import os
from uuid import uuid4

import pytest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import engine as configured_engine


@pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_E2E") != "1",
    reason="Set RUN_POSTGRES_E2E=1",
)
def test_real_postgresql_remittance_lock_isolation():
    asyncio.run(_run_lock_test())


async def _run_lock_test():
    engine = create_async_engine(
        configured_engine.url,
        poolclass=NullPool,
        echo=False,
    )

    random_key = uuid4().int

    # PostgreSQL advisory lock (int4,int4)
    # keys must fit within signed int32.
    company_key = (random_key % 1000000000) + 100000
    instruction_key = (
        (random_key >> 32) % 1000000000
    ) + 100000

    other_company = company_key + 1
    other_instruction = instruction_key + 1

    try:
        async with engine.connect() as first:
            first_tx = await first.begin()

            try:
                await first.execute(
                    text("""
                        SELECT pg_advisory_xact_lock(
                            :company,
                            :instruction
                        )
                    """),
                    {
                        "company": company_key,
                        "instruction": instruction_key,
                    },
                )

                print("FIRST TRANSACTION LOCK = ACQUIRED")

                async with engine.connect() as second:
                    second_tx = await second.begin()

                    try:
                        same_source = (
                            await second.execute(
                                text("""
                                    SELECT pg_try_advisory_xact_lock(
                                        :company,
                                        :instruction
                                    )
                                """),
                                {
                                    "company": company_key,
                                    "instruction": instruction_key,
                                },
                            )
                        ).scalar_one()

                        assert same_source is False

                        print("SAME-SOURCE CONCURRENT LOCK = BLOCKED")

                        other_source = (
                            await second.execute(
                                text("""
                                    SELECT pg_try_advisory_xact_lock(
                                        :company,
                                        :instruction
                                    )
                                """),
                                {
                                    "company": company_key,
                                    "instruction": other_instruction,
                                },
                            )
                        ).scalar_one()

                        assert other_source is True

                        print("OTHER INSTRUCTION = INDEPENDENT")

                        other_tenant = (
                            await second.execute(
                                text("""
                                    SELECT pg_try_advisory_xact_lock(
                                        :company,
                                        :instruction
                                    )
                                """),
                                {
                                    "company": other_company,
                                    "instruction": instruction_key,
                                },
                            )
                        ).scalar_one()

                        assert other_tenant is True

                        print("OTHER COMPANY = INDEPENDENT")

                    finally:
                        await second_tx.rollback()

            finally:
                if first_tx.is_active:
                    await first_tx.rollback()

        # A fresh connection must be able to acquire the
        # previously contested lock after rollback.

        async with engine.connect() as third:
            third_tx = await third.begin()

            try:
                released = (
                    await third.execute(
                        text("""
                            SELECT pg_try_advisory_xact_lock(
                                :company,
                                :instruction
                            )
                        """),
                        {
                            "company": company_key,
                            "instruction": instruction_key,
                        },
                    )
                ).scalar_one()

                assert released is True

                print("TRANSACTION ROLLBACK RELEASES LOCK = PASS")

            finally:
                await third_tx.rollback()

        print("REAL POSTGRESQL LOCK ISOLATION = PASS")
        print("DATABASE BUSINESS WRITES = 0")

    finally:
        await engine.dispose()
