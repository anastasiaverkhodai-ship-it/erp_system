import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.services.payroll_regulatory_d5_d6_source_control import (
    D5D6SourceError,
    build_d5_d6_source_control,
)


pytestmark = pytest.mark.asyncio


async def test_real_postgresql_d5_d6_source_inventory(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        result = await build_d5_d6_source_control(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        assert result["review_only"] is True
        assert result["official_statutory_generation"] == "BLOCKED"
        assert result["d5"]["official_rows_generated"] == 0
        assert result["d6"]["official_rows_generated"] == 0
        assert result["d6"]["applicability"] == "NOT_DETERMINED"

        for table in result["source_inventory"]:
            for row in (
                table["dated_evidence"]
                + table["undated_evidence"]
            ):
                assert row["company_id"] == company.id
                assert len(row["source_sha256"]) == 64
                assert row["review_only"] is True

        await db.rollback()


async def test_cross_company_period_rejected(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        with pytest.raises(D5D6SourceError):
            await build_d5_d6_source_control(
                db,
                company_id=company.id + 100000,
                payroll_period_id=calculation.payroll_period_id,
            )

        await db.rollback()
