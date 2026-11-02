"""Read-only real PostgreSQL regulatory privilege acceptance."""

import os
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine

from app.services.payroll_regulatory_security_readiness import (
    review_postgresql_snapshot_security,
    regulatory_readiness,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Real PostgreSQL required",
    ),
]


async def test_real_snapshot_privileges_and_regulatory_blockers(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        database = await db.scalar(
            text("SELECT current_database()")
        )
        assert database.startswith("erp_r3_copy_")

        security = await review_postgresql_snapshot_security(db)
        evidence = security["database_evidence"]

        assert evidence["snapshot_table_exists"] is True
        assert evidence["trigger_enabled"] is True

        print("RUNTIME ROLE =", evidence["runtime_role"])
        print("TABLE OWNER =", evidence["table_owner"])
        print("ROLE SECURITY STATUS =", security["security_status"])
        print("ROLE SECURITY FAILURES =", security["failures"])

        readiness = regulatory_readiness(security=security)

        for report in ("d1", "4df", "d5", "d6"):
            row = readiness["reports"][report]
            assert row["official_generation"] == "BLOCKED"
            assert row["blockers"]
            print(
                report.upper(),
                "OFFICIAL READINESS = BLOCKED",
            )

        assert readiness["all_reports_officially_ready"] is False
        assert readiness["official_generation"] == "BLOCKED"
        print("REAL PG SECURITY EVIDENCE = VERIFIED")
        print("FOUR-REPORT FAIL-CLOSED CONTROL = PASS")

        await db.rollback()
