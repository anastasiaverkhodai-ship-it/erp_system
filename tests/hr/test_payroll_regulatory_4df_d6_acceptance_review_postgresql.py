"""D1/4DF/D6 integrated real PostgreSQL review tests."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_d1_completeness_postgresql import (
    prepare,
)

from app.services.payroll_regulatory_4df_d6_acceptance_review import (
    build_4df_d6_acceptance_review,
    verify_4df_d6_acceptance_review,
)


pytestmark = pytest.mark.asyncio


async def test_real_pg_4df_d6_review_stays_internal(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)

        report = await build_4df_d6_acceptance_review(
            db,
            company_id=company_id,
            payroll_period_id=period_id,
        )

        assert report["d1_source_integrity"] == "PASS"
        assert report["four_df_posted_payment_status"] == (
            "NOT_SUPPLIED"
        )
        assert report["four_df_gross_paid_status"] == (
            "NOT_NORMATIVELY_VERIFIED"
        )
        assert report["d6_normative_status"] == "NOT_APPROVED"
        assert report["official_generation"] == "BLOCKED"

        assert await verify_4df_d6_acceptance_review(
            db, report
        ) is True

        await db.rollback()


async def test_real_pg_cross_company_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)

        with pytest.raises(Exception):
            await build_4df_d6_acceptance_review(
                db,
                company_id=company_id + 100000,
                payroll_period_id=period_id,
            )

        await db.rollback()
