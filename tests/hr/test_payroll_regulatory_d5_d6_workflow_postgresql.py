import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.services.payroll_regulatory_d5_d6_workflow import (
    D5D6WorkflowError,
    build_verified_d5_d6_workflow,
    compare_hr_reviews,
    verify_review_snapshot,
)


pytestmark = pytest.mark.asyncio


async def test_real_postgresql_d5_d6_workflow(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        result = await build_verified_d5_d6_workflow(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        assert verify_review_snapshot(result)
        assert result["company_id"] == company.id
        assert result["review_only"] is True
        assert result["overall_status"] == "REVIEW_ONLY"

        assert result["d5"]["official_rows_generated"] == 0
        assert result["d6"]["official_rows_generated"] == 0

        assert result["d6"]["applicability"] == "NOT_DETERMINED"

        identical = await build_verified_d5_d6_workflow(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        comparison = compare_hr_reviews(result, identical)

        assert comparison["change_count"] == 0
        assert comparison["statutory_correction_generated"] is False

        await db.rollback()


async def test_cross_company_postgresql_workflow_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        with pytest.raises(Exception):
            await build_verified_d5_d6_workflow(
                db,
                company_id=company.id + 100000,
                payroll_period_id=calculation.payroll_period_id,
            )

        await db.rollback()
