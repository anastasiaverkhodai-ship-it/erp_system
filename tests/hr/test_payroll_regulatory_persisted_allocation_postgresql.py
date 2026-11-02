import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)

from app.services.payroll_regulatory_persisted_allocation import (
    reconcile_persisted_allocations,
)


pytestmark = pytest.mark.asyncio


async def test_real_persisted_regulatory_allocation_read(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        result = await reconcile_persisted_allocations(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        assert result["company_id"] == company.id
        assert result["official_generation"] == "BLOCKED"

        assert result["status"] in (
            "NOT_VERIFIED",
            "FINANCIAL_LINES_RECONCILED",
        )

        if result["status"] == "FINANCIAL_LINES_RECONCILED":
            assert result["data_origin"] == "PERSISTED_POSTGRESQL"
            assert result["verified_lines"] > 0
            assert result["statutory_mapping_verified"] is False

        else:
            assert result.get("reason") or result.get("lines")

        await db.rollback()


async def test_cross_company_period_not_accepted(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        try:
            result = await reconcile_persisted_allocations(
                db,
                company_id=company.id + 100000,
                payroll_period_id=calculation.payroll_period_id,
            )
        except FinancialReconciliationError:
            pass
        else:
            assert result["status"] == "NOT_VERIFIED"

        await db.rollback()
