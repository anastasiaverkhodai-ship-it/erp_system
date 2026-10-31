import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.services.payroll_regulatory_d1_review_projection import (
    build_verified_d1_review,
)


pytestmark = pytest.mark.asyncio


async def test_real_d1_sources_and_usc_reconcile(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        # This test needs a complete real statutory source.
        # The synthetic tax identifier is used exclusively in the
        # isolated, rollback-only PostgreSQL test database.
        from app.models.employee import Employee
        from app.services.payroll_regulatory_readiness_service import (
            payroll_regulatory_readiness,
        )

        employee = await db.get(Employee, contract.employee_id)

        assert employee is not None
        assert employee.company_id == company.id

        if not employee.tax_number:
            employee.tax_number = "1234567890"
            await db.flush()

        readiness = await payroll_regulatory_readiness(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
            report_kind="d1",
        )

        assert readiness["calculation_sources"]

        for source_row in readiness["calculation_sources"]:
            assert not source_row["issues"], (
                "Incomplete real payroll test source: "
                + repr(source_row["issues"])
            )


        result = await build_verified_d1_review(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        assert result["report_kind"] == "d1"
        assert result["review_only"] is True
        assert result["approved_normative_mapping"] is False
        assert result["official_statutory_generation"] == "BLOCKED"
        assert result["source_count"] > 0
        assert result["rows"]

        calculation_ids = {
            row["payroll_calculation_id"]
            for row in result["rows"]
        }

        assert calculation.id in calculation_ids

        for row in result["rows"]:
            assert row["company_id"] == company.id
            assert row["payroll_period_id"] == (
                calculation.payroll_period_id
            )
            assert row["usc_evidence"]
            assert row["review_only"] is True

        await db.rollback()


async def test_cross_company_d1_sources_rejected(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        with pytest.raises(Exception):
            await build_verified_d1_review(
                db,
                company_id=company.id + 1000000,
                payroll_period_id=calculation.payroll_period_id,
            )

        await db.rollback()
