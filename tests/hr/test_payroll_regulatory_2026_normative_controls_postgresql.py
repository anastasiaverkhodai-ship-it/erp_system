"""Real PostgreSQL provenance with conservative normative gates."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_d1_completeness_postgresql import (
    prepare,
)

from app.models.payroll import PayrollPeriod
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_persisted_source_verifier import (
    verify_persisted_payroll_sources,
)
from app.services.payroll_regulatory_2026_normative_controls import (
    build_normative_review,
    verify_normative_review,
)


pytestmark = pytest.mark.asyncio


async def test_pg_live_payroll_with_2026_normative_gate(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)

        period = await db.scalar(
            select(PayrollPeriod).where(
                PayrollPeriod.company_id == company_id,
                PayrollPeriod.id == period_id,
            )
        )
        assert period is not None

        readiness = await payroll_regulatory_readiness(
            db,
            company_id=company_id,
            payroll_period_id=period_id,
            report_kind="4df",
        )

        checked = await verify_persisted_payroll_sources(
            db,
            company_id=company_id,
            payroll_period_id=period_id,
            readiness=readiness,
        )
        assert checked["verified"] is True

        # Normative illustration is explicitly September 2026;
        # it does not change the seeded payroll period.
        review = build_normative_review(
            company_id=company_id,
            payroll_period_id=period_id,
            month="2026-09",
        )

        assert verify_normative_review(review) is True
        assert review["body"]["four_df"][
            "statutory_gross_paid_approved"
        ] is False
        assert review["body"]["official_generation"] == "BLOCKED"

        await db.rollback()


async def test_pg_no_d6_approval_without_eligibility(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)

        review = build_normative_review(
            company_id=company_id,
            payroll_period_id=period_id,
            month="2026-09",
            d6_special_service_positions=None,
        )

        assert review["body"]["d6"]["d6_generation"] == (
            "BLOCKED"
        )
        assert verify_normative_review(review) is True

        await db.rollback()
