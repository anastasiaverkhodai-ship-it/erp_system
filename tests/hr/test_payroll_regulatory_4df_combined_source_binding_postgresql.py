"""PostgreSQL source-binding gate; never files official 4DF."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_d1_completeness_postgresql import (
    prepare,
)

from app.services.payroll_regulatory_4df_combined_source_binding import (
    FourDFCombinedBindingError,
    build_4df_combined_source_binding,
    verify_4df_combined_source_binding,
)


pytestmark = pytest.mark.asyncio


async def test_pg_unprovided_slices_remain_blocked(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)

        report = await build_4df_combined_source_binding(
            db,
            company_id=company_id,
            payroll_period_id=period_id,
        )

        assert report["body"]["four_df_status"] == (
            "NOT_VERIFIED"
        )
        assert report["body"]["source_binding"] is None
        assert report["body"]["gross_paid_income_verified"] is False
        assert report["body"]["official_generation"] == "BLOCKED"

        assert await verify_4df_combined_source_binding(
            db, report
        ) is True

        await db.rollback()


async def test_pg_cross_company_source_scope_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)

        with pytest.raises(FourDFCombinedBindingError):
            await build_4df_combined_source_binding(
                db,
                company_id=company_id + 100000,
                payroll_period_id=period_id,
            )

        await db.rollback()
