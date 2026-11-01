from copy import deepcopy

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.services.payroll_regulatory_unified_review import (
    build_unified_regulatory_review,
)
from app.services.payroll_regulatory_correction_review import (
    RegulatoryCorrectionReviewError,
    build_regulatory_correction_review,
    compare_regulatory_snapshots,
    snapshot_regulatory_review,
)


pytestmark = pytest.mark.asyncio


async def test_four_report_correction_requery_postgresql(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        before = await build_unified_regulatory_review(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        result = await build_regulatory_correction_review(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
            before_review=before,
        )

        comparison = result["comparison"]

        assert comparison["changed_report_count"] == 0
        assert comparison["statutory_correction_generated"] is False
        assert comparison["official_generation"] == "BLOCKED"
        assert comparison["financial_reconciliation"] == "NOT_RECOMPUTED"

        assert set(result["current_snapshot"]["section_digests"]) == {
            "d1", "4df", "d5", "d6"
        }

        # Change review evidence in memory only, then prove that the
        # deterministic correction detector finds the changed section.
        changed = deepcopy(before)
        changed["reports"]["d1"]["source_readiness"][
            "_test_only_changed_source"
        ] = "modified"

        detected = compare_regulatory_snapshots(
            snapshot_regulatory_review(before),
            snapshot_regulatory_review(changed),
        )

        assert detected["changed_report_count"] == 1
        assert detected["changed_reports"][0]["report_kind"] == "d1"

        await db.rollback()


async def test_cross_company_requery_rejected(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        before = await build_unified_regulatory_review(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        with pytest.raises(RegulatoryCorrectionReviewError):
            await build_regulatory_correction_review(
                db,
                company_id=company.id + 10000,
                payroll_period_id=calculation.payroll_period_id,
                before_review=before,
            )

        await db.rollback()
