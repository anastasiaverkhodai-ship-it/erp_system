"""Real PostgreSQL evidence and cross-company negative test."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_d1_completeness_postgresql import (
    prepare,
)

from app.services.payroll_regulatory_withholding_source_reconciliation import (
    WithholdingReconciliationError,
    build_source_reconciliation,
    verify_source_reconciliation,
)
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)


pytestmark = pytest.mark.asyncio


async def test_pg_source_bound_real_payroll(employee_engine):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)

        readiness = await payroll_regulatory_readiness(
            db,
            company_id=company_id,
            payroll_period_id=period_id,
            report_kind="4df",
        )

        calculation_id = readiness[
            "calculation_sources"
        ][0]["payroll_calculation_id"]

        result = await build_source_reconciliation(
            db,
            company_id=company_id,
            payroll_calculation_id=calculation_id,
        )

        assert verify_source_reconciliation(result)
        assert result["body"][
            "reconciliation"
        ]["payment_specific_withholding"] == "NOT_VERIFIED"
        assert result["body"]["official_generation"] == "BLOCKED"

        await db.rollback()


async def test_pg_cross_company_calculation_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)

        readiness = await payroll_regulatory_readiness(
            db,
            company_id=company_id,
            payroll_period_id=period_id,
            report_kind="4df",
        )

        calculation_id = readiness[
            "calculation_sources"
        ][0]["payroll_calculation_id"]

        with pytest.raises(
            WithholdingReconciliationError,
            match="not found",
        ):
            await build_source_reconciliation(
                db,
                company_id=company_id + 999999,
                payroll_calculation_id=calculation_id,
            )

        await db.rollback()
