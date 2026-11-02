import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
    inspect_persisted_financial_sources,
    reconcile_lines,
)


pytestmark = pytest.mark.asyncio


async def test_real_postgresql_financial_source_inventory(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        period_id = calculation.payroll_period_id

        inspection = await inspect_persisted_financial_sources(
            db,
            company_id=company.id,
            payroll_period_id=period_id,
        )

        assert inspection["company_id"] == company.id
        assert inspection["payroll_period_id"] == period_id
        assert inspection["official_generation"] == "BLOCKED"
        assert inspection["status"] == "NOT_VERIFIED"

        assert "payroll_statutory_result_lines" in (
            inspection["columns"]
        )

        # Demonstrate independent amount calculation without
        # claiming that synthetic data are official report rows.
        independent = reconcile_lines(
            [{
                "line_id": 901,
                "company_id": company.id,
                "payroll_period_id": period_id,
                "reported_amount": "100.00",
                "sources": [
                    {
                        "source_id": 1001,
                        "source_amount": "75.00",
                    },
                    {
                        "source_id": 1002,
                        "source_amount": "25.00",
                    },
                ],
            }],
            company_id=company.id,
            payroll_period_id=period_id,
        )

        assert independent["status"] == (
            "FINANCIAL_LINES_RECONCILED"
        )

        await db.rollback()


async def test_cross_company_period_rejected(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        with pytest.raises(FinancialReconciliationError):
            await inspect_persisted_financial_sources(
                db,
                company_id=company.id + 100000,
                payroll_period_id=calculation.payroll_period_id,
            )

        await db.rollback()
