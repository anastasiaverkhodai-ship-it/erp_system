"""Integrated D1/4DF financial control using actual correction records."""

import os
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_correction_basis_postgresql import prepare, protect

from app.models.payroll import PayrollPeriod
from app.models.payroll_statutory import PayrollStatutoryComponent

from app.services.payroll_statutory_service import (
    create_statutory_rate,
)
from app.services.payroll_dated_employee_statutory import (
    write_dated_employee_statutory_result,
)
from app.services.payroll_correction_recalculation_service import (
    recalculate_minimum_wage_correction,
)
from app.services.payroll_regulatory_correction_report_control import (
    review_persisted_correction_report,
)
from decimal import Decimal

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Real PostgreSQL required",
    ),
]


async def test_d1_and_4df_real_correction_control(employee_engine):
    await protect(employee_engine)

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        fixture, source, original, evidence, args = await prepare(db)
        company_id = fixture["company"]
        actor_id = fixture["actor"]

        period = await db.get(
            PayrollPeriod, original.payroll_period_id
        )

        for component, rate in (
            (PayrollStatutoryComponent.PERSONAL_INCOME_TAX, Decimal(".18")),
            (PayrollStatutoryComponent.MILITARY_LEVY, Decimal(".05")),
            (PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION, Decimal(".22")),
        ):
            await create_statutory_rate(
                db,
                company_id=company_id,
                component=component,
                rate=rate,
                effective_from=period.start_date,
                effective_to=None,
                actor_user_id=actor_id,
            )

        await db.flush()

        original_result = await write_dated_employee_statutory_result(
            db,
            company_id=company_id,
            payroll_calculation_id=original.id,
            actor_user_id=actor_id,
        )

        replacement, correction = (
            await recalculate_minimum_wage_correction(
                db, **args
            )
        )

        replacement_result = await write_dated_employee_statutory_result(
            db,
            company_id=company_id,
            payroll_calculation_id=replacement.id,
            actor_user_id=actor_id,
        )

        assert original_result.id != replacement_result.id
        assert replacement.revision == 2
        assert original.gross_amount == Decimal("4323.50")
        assert replacement.gross_amount == Decimal("4755.85")

        for report_kind in ("d1", "4df"):
            result = await review_persisted_correction_report(
                db,
                company_id=company_id,
                payroll_period_id=period.id,
                report_kind=report_kind,
            )

            print(
                report_kind.upper(),
                "CORRECTION FINANCIAL STATUS =",
                result["financial_status"],
            )

            assert result["financial_status"] == (
                "REPORT_CORRECTION_FINANCIALS_RECONCILED"
            ), result

            assert result["financial_correction_verified"] is True
            assert result["active_calculation_ids"] == [replacement.id]
            assert result["excluded_historical_calculation_ids"] == [
                original.id
            ]
            assert result["official_form_fields_verified"] is False
            assert result["official_generation"] == "BLOCKED"

        print("REAL D1/4DF CORRECTION FINANCIAL CONTROL = PASS")
        print("REAL ORIGINAL AND REPLACEMENT SOURCES = PASS")
        print("REAL HISTORICAL DOUBLE COUNT CONTROL = PASS")

        await db.rollback()
