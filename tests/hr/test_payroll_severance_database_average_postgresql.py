"""Read-only PostgreSQL contract for severance average derivation."""

import os
import re
from datetime import date

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
)
from app.services.payroll_severance_database_average import (
    derive_database_severance_average,
)


pytestmark = pytest.mark.asyncio


def clone_url():
    assert os.getenv("RUN_POSTGRES_E2E") == "1"

    configured = make_url(str(settings.database_url))

    assert re.fullmatch(
        r"erp_r3_copy_[0-9a-f]{16}",
        configured.database or "",
    )
    assert configured.host in (
        "localhost",
        "127.0.0.1",
        "::1",
    )

    return str(settings.database_url)


async def test_real_database_source_contract():
    engine = create_async_engine(
        clone_url(),
        pool_pre_ping=True,
    )

    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name IN (
                            'employment_contracts',
                            'payroll_periods',
                            'payroll_inputs',
                            'payroll_calculations',
                            'payroll_calculation_lines',
                            'payroll_earnings_history',
                            'attendance_records',
                            'employment_schedule_assignments',
                            'work_schedule_days'
                          )
                        """
                    )
                )
            ).scalars().all()

            expected = {
                "employment_contracts",
                "payroll_periods",
                "payroll_inputs",
                "payroll_calculations",
                "payroll_calculation_lines",
                "payroll_earnings_history",
                "attendance_records",
                "employment_schedule_assignments",
                "work_schedule_days",
            }

            assert expected <= set(rows), expected - set(rows)

            # Choose a company/contract pair which cannot correspond
            # to a real record. Reject before reading payroll history.
            async with engine.connect() as probe:
                largest_company = await probe.scalar(
                    text("SELECT COALESCE(MAX(id),0) FROM companies")
                )
                largest_contract = await probe.scalar(
                    text(
                        "SELECT COALESCE(MAX(id),0) "
                        "FROM employment_contracts"
                    )
                )

            absent_company = int(largest_company) + 1
            absent_contract = int(largest_contract) + 1

            from sqlalchemy.ext.asyncio import AsyncSession

            async with AsyncSession(engine) as db:
                with pytest.raises(
                    SeveranceAverageError,
                    match="Company-scoped employment contract not found",
                ):
                    await derive_database_severance_average(
                        db,
                        company_id=absent_company,
                        employment_contract_id=absent_contract,
                        termination_date=date(2026, 9, 30),
                    )

                await db.rollback()

        print("REAL POSTGRESQL SCHEMA = PASS")
        print("CROSS-COMPANY / MISSING CONTRACT = BLOCKED")
        print("PRODUCTION TABLE WRITES = 0")

    finally:
        await engine.dispose()
