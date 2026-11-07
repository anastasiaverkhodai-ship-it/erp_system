"""Real PostgreSQL fail-closed D5 readiness integration."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)


pytestmark = pytest.mark.asyncio


async def test_d5_normative_readiness_real_postgresql(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        result = await payroll_regulatory_readiness(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
            report_kind="d5",
        )

        assert result["report_kind"] == "d5"
        assert result["ready"] is False

        assert (
            "D5_NORMATIVE_SOURCE_CHAIN_NOT_APPROVED"
            in result["blocking_issues"]
        )

        assert (
            "D5_PROFESSIONAL_DETAILS_AND_EVENT_MAPPING_MISSING"
            in result["blocking_issues"]
        )

        evidence = result["d5_normative_evidence"]

        assert evidence["status"] == "REVIEW_REQUIRED"
        assert evidence["official_generation"] == "BLOCKED"
        assert evidence["official_rows_generated"] == 0
        assert evidence["candidate_count"] == 0
        assert len(evidence["missing_source_kinds"]) == 5

        # The pre-existing worktree reconciliation
        # must remain operational or fail explicitly.
        assert (
            "source_reconciliation" in result
            or "STATUTORY_SOURCE_RECONCILIATION_FAILED"
            in result["blocking_issues"]
        )

        await db.rollback()
