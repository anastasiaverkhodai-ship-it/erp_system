import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.models.employee import Employee
from app.services.payroll_regulatory_d1_dated_sources import (
    build_d1_dated_source_review,
)


pytestmark = pytest.mark.asyncio


async def create_case(db):
    company, user, contract, calculation, *_ = await seed(db)

    employee = await db.get(Employee, contract.employee_id)

    assert employee is not None
    assert employee.company_id == company.id

    if not employee.tax_number:
        employee.tax_number = "1234567890"
        await db.flush()

    return company, calculation


async def test_real_postgresql_d1_dated_review(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, calculation = await create_case(db)

        result = await build_d1_dated_source_review(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        assert result["report_kind"] == "d1"
        assert result["review_only"] is True
        assert result["official_statutory_generation"] == "BLOCKED"

        assert result["d1_financial_reconciliation"] == "PASS"

        assert result["dated_statutory_line_count"] > 0

        assert result["dated_provenance_status"] in (
            "COMPLETE",
            "INCOMPLETE",
        )

        assert result["d6_control"]["status"] == "NOT_VERIFIED"

        for item in result["dated_controls"]:
            assert item["payroll_calculation_id"] > 0
            assert item["statutory_line_id"] > 0

        await db.rollback()


async def test_cross_company_dated_review_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, calculation = await create_case(db)

        with pytest.raises(Exception):
            await build_d1_dated_source_review(
                db,
                company_id=company.id + 100000,
                payroll_period_id=calculation.payroll_period_id,
            )

        await db.rollback()
