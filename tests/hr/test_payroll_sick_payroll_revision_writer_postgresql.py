"""Isolated PostgreSQL writer rejection: missing application.

This is a negative DB E2E, not a positive persistence acceptance.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine

from app.services.payroll_sick_payroll_revision_writer import (
    persist_sick_payroll_revision,
)
from app.services.payroll_sick_payroll_replay import (
    SickPayrollReplayError,
)

pytestmark = pytest.mark.asyncio


async def test_missing_application_refused(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        from test_payroll_secondary_sick_postgresql import prepare

        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        assert company.id is not None

        with pytest.raises(
            SickPayrollReplayError,
            match="application not found",
        ):
            await persist_sick_payroll_revision(
                db,
                company_id=company.id,
                application_id=2147483647,
                original_payroll_id=2147483647,
                created_by=1,
            )
