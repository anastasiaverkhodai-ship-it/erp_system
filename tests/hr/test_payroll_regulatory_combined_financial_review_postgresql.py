"""Real PostgreSQL combined D1/4DF/D6 review acceptance."""

from copy import deepcopy

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_d1_completeness_postgresql import (
    prepare,
)

from app.services.payroll_regulatory_combined_financial_review import (
    CombinedFinancialReviewError,
    build_combined_financial_review,
    validate_combined_review,
    verify_combined_financial_review,
)


pytestmark = pytest.mark.asyncio


async def test_pg_combined_review_and_stale_guard(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, period_id = await prepare(db)

        review = await build_combined_financial_review(
            db,
            company_id=company_id,
            payroll_period_id=period_id,
        )

        assert validate_combined_review(review) is True
        assert review["d1"]["source_integrity"] == "PASS"
        assert review["four_df"]["status"] == "NOT_VERIFIED"
        assert review["d6"]["normative_mapping"] == (
            "NOT_APPROVED"
        )
        assert review["official_generation"] == "BLOCKED"

        assert await verify_combined_financial_review(
            db, review
        ) is True

        altered = deepcopy(review)
        altered["payroll"]["gross"] = "999999.00"

        with pytest.raises(
            CombinedFinancialReviewError,
            match="digest mismatch",
        ):
            validate_combined_review(altered)

        await db.rollback()


async def test_pg_cross_company_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, period_id = await prepare(db)

        with pytest.raises(
            CombinedFinancialReviewError,
            match="financial review rejected",
        ):
            await build_combined_financial_review(
                db,
                company_id=company_id + 100000,
                payroll_period_id=period_id,
            )

        await db.rollback()
