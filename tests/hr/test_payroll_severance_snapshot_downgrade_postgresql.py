"""Isolated-only severance snapshot empty downgrade/upgrade test."""

import os
import re
import subprocess
import sys

from sqlalchemy.engine import make_url
from app.core.config import settings


OLD = "13d4a7b8c041"
NEW = "13d4a7b8c042"


def test_empty_severance_snapshot_downgrade_and_restore():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    configured = make_url(str(settings.database_url))
    environment = make_url(os.environ["DATABASE_URL"])

    for url in (configured, environment):
        assert re.fullmatch(
            r"erp_r3_copy_[0-9a-f]{16}",
            url.database or "",
        )
        assert url.host in ("localhost", "127.0.0.1", "::1")

    assert configured.database == environment.database

    def alembic(*arguments):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=os.environ.copy(),
        )

    def revision():
        result = alembic("current")
        assert result.returncode == 0, (
            result.stdout, result.stderr
        )
        versions = re.findall(
            r"(?m)^\s*([0-9a-f]{12})\b",
            result.stdout,
        )
        assert len(versions) == 1, result.stdout
        return versions[0]

    assert revision() == NEW

    # Existing runner copies from a c041 database:
    # c042 snapshots must be empty before this test.
    import asyncio
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def inspect_table():
        engine = create_async_engine(str(settings.database_url))
        try:
            async with engine.connect() as conn:
                rows = await conn.scalar(text(
                    "SELECT count(*) FROM "
                    "payroll_severance_statutory_snapshots"
                ))
                return rows
        finally:
            await engine.dispose()

    assert asyncio.run(inspect_table()) == 0

    try:
        down = alembic("downgrade", OLD)
        assert down.returncode == 0, (
            down.stdout, down.stderr
        )
        assert revision() == OLD

        async def table_absent():
            engine = create_async_engine(str(settings.database_url))
            try:
                async with engine.connect() as conn:
                    return await conn.scalar(text(
                        "SELECT to_regclass("
                        "'public.payroll_severance_statutory_snapshots'"
                        ")"
                    ))
            finally:
                await engine.dispose()

        assert asyncio.run(table_absent()) is None

    finally:
        # Restore the disposable clone for cleanup and final checks.
        if revision() == OLD:
            up = alembic("upgrade", NEW)
            assert up.returncode == 0, (
                up.stdout, up.stderr
            )

    assert revision() == NEW

    check = alembic("check")
    assert check.returncode == 0, (
        check.stdout, check.stderr
    )

    print("EMPTY DOWNGRADE c042 -> c041 = PASS")
    print("SNAPSHOT TABLE REMOVED = PASS")
    print("RESTORE c041 -> c042 = PASS")
    print("ALEMBIC CHECK AFTER RESTORE = PASS")
