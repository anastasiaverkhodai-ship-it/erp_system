import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.services.payroll_regulatory_unified_review import (
    build_unified_regulatory_review,
    validate_unified_review,
)


pytestmark = pytest.mark.asyncio


async def test_unified_four_report_postgresql(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        result = await build_unified_regulatory_review(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        assert validate_unified_review(result)

        assert set(result["reports"]) == {
            "d1", "4df", "d5", "d6"
        }

        assert result["official_generation"] == "BLOCKED"

        assert result["reports"]["d5"][
            "hr_source_snapshot_sha256"
        ]

        assert result["reports"]["d6"][
            "applicability"
        ] == "NOT_DETERMINED"

        await db.rollback()


async def test_unified_review_invalid_company_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        with pytest.raises(Exception):
            await build_unified_regulatory_review(
                db,
                company_id=company.id + 100000,
                payroll_period_id=calculation.payroll_period_id,
            )

        await db.rollback()
