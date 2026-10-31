import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.models.employee import Employee

from app.services.payroll_regulatory_d1_completeness import (
    build_d1_completeness_control,
)


pytestmark = pytest.mark.asyncio


async def prepare(db):
    company, user, contract, calculation, *_ = await seed(db)

    employee = await db.get(Employee, contract.employee_id)
    assert employee is not None
    assert employee.company_id == company.id

    if not employee.tax_number:
        employee.tax_number = "1234567890"
        await db.flush()

    return company.id, calculation.payroll_period_id


async def test_d1_completeness_with_real_postgresql(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, period_id = await prepare(db)

        result = await build_d1_completeness_control(
            db,
            company_id=company_id,
            payroll_period_id=period_id,
        )

        assert result["overall_status"] == "REVIEW_ONLY"
        assert result["official_statutory_generation"] == "BLOCKED"

        d1 = result["d1"]
        assert d1["source_integrity"] == "PASS"
        assert d1["statutory_line_count"] > 0

        assert d1["d1_status"] in (
            "SOURCE_COVERAGE_PRESENT",
            "SOURCE_COVERAGE_INCOMPLETE",
        )

        assert d1["statutory_base_allocation_verified"] is False
        assert result["d5"]["status"] == "NOT_VERIFIED"
        assert result["d6"]["status"] == "NOT_VERIFIED"

        await db.rollback()


async def test_cross_company_d1_control_rejected(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, period_id = await prepare(db)

        with pytest.raises(Exception):
            await build_d1_completeness_control(
                db,
                company_id=company_id + 100000,
                payroll_period_id=period_id,
            )

        await db.rollback()
