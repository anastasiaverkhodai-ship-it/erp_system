"""Integrated real PostgreSQL correction and dated tax acceptance.

Uses the protected dated statutory writer for original and
replacement calculations. No manual statutory allocations.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_correction_basis_postgresql import prepare, protect

from app.models.payroll import PayrollPeriod
from app.models.payroll_correction import PayrollCorrection
from app.models.payroll_statutory import (
    PayrollStatutoryComponent,
    PayrollStatutoryResultLine,
    PayrollStatutoryLineSource,
)

from app.services.payroll_statutory_service import create_statutory_rate
from app.services.payroll_dated_employee_statutory import (
    write_dated_employee_statutory_result,
)
from app.services.payroll_correction_source_evidence_service import (
    capture_payroll_correction_source,
)
from app.services.payroll_correction_recalculation_service import (
    recalculate_minimum_wage_correction,
)
from app.services.payroll_regulatory_revision_chain_review import (
    review_persisted_revision_chain,
)
from app.services.payroll_regulatory_persisted_correction_delta import (
    review_persisted_correction_delta,
)
from app.services.payroll_regulatory_active_financial_review import (
    review_persisted_active_financials,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Real isolated PostgreSQL required",
    ),
]


async def source_count(db, company_id, result_id):
    return await db.scalar(
        select(func.count(PayrollStatutoryLineSource.id))
        .join(
            PayrollStatutoryResultLine,
            (
                PayrollStatutoryResultLine.company_id
                == PayrollStatutoryLineSource.company_id
            )
            & (
                PayrollStatutoryResultLine.id
                == PayrollStatutoryLineSource.statutory_line_id
            ),
        )
        .where(
            PayrollStatutoryResultLine.company_id == company_id,
            PayrollStatutoryResultLine.payroll_statutory_result_id
            == result_id,
        )
    )


async def test_full_real_dated_correction_acceptance(employee_engine):
    await protect(employee_engine)

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        fixture, payroll_input, original, _, args = await prepare(db)

        company_id = fixture["company"]
        actor_id = fixture["actor"]

        period = await db.get(
            PayrollPeriod,
            original.payroll_period_id,
        )
        assert period is not None

        for component, rate in (
            (PayrollStatutoryComponent.PERSONAL_INCOME_TAX,
             Decimal("0.18")),
            (PayrollStatutoryComponent.MILITARY_LEVY,
             Decimal("0.05")),
            (PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION,
             Decimal("0.22")),
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

        # The original is still the current revision here.
        # The verified dated writer must create real source links.
        original_tax = await write_dated_employee_statutory_result(
            db,
            company_id=company_id,
            payroll_calculation_id=original.id,
            actor_user_id=actor_id,
        )

        original_sources = await source_count(
            db, company_id, original_tax.id
        )

        print("ORIGINAL AUTHENTIC DATED SOURCES =", original_sources)

        assert original_sources > 0, (
            "Verified dated writer did not attribute original income"
        )

        source = await capture_payroll_correction_source(
            db,
            company_id=company_id,
            original_payroll_calculation_id=original.id,
            request_key=args["request_key"],
            reason=args["reason"],
        )

        assert source["source_evidence_complete"] is True

        revised_args = dict(args)
        revised_args["expected_source_fingerprint_sha256"] = (
            source["source_fingerprint_sha256"]
        )

        replacement, correction = (
            await recalculate_minimum_wage_correction(
                db,
                **revised_args,
            )
        )

        assert original.revision == 1
        assert replacement.revision == 2
        assert correction.original_payroll_calculation_id == original.id
        assert correction.replacement_payroll_calculation_id == (
            replacement.id
        )

        assert original.gross_amount == Decimal("4323.50")
        assert replacement.gross_amount == Decimal("4755.85")

        gross_delta = (
            replacement.gross_amount - original.gross_amount
        )

        assert gross_delta == Decimal("432.35")

        replacement_tax = await write_dated_employee_statutory_result(
            db,
            company_id=company_id,
            payroll_calculation_id=replacement.id,
            actor_user_id=actor_id,
        )

        replacement_sources = await source_count(
            db, company_id, replacement_tax.id
        )

        print(
            "REPLACEMENT AUTHENTIC DATED SOURCES =",
            replacement_sources,
        )

        assert replacement_sources > 0
        assert replacement_tax.id != original_tax.id

        chain = await review_persisted_revision_chain(
            db,
            company_id=company_id,
            payroll_period_id=period.id,
        )

        assert chain["status"] == "REVISION_CHAIN_VERIFIED", chain
        assert chain["active_calculation_ids"] == [replacement.id]
        assert chain["historical_calculation_ids"] == [original.id]
        assert chain["correction_count"] == 1

        delta = await review_persisted_correction_delta(
            db,
            company_id=company_id,
            payroll_period_id=period.id,
        )

        print("CORRECTION DELTA STATUS =", delta["status"])
        print("CORRECTION DELTA COMPONENTS =", delta.get("components"))

        assert delta["status"] == (
            "CORRECTION_COMPONENT_DELTA_RECONCILED"
        ), delta
        assert delta["persisted_correction_delta_verified"] is True
        assert delta["missing_source_line_ids"] == []
        assert delta["mismatched_source_line_ids"] == []
        assert delta["active_calculation_ids"] == [replacement.id]
        assert delta["historical_calculation_ids"] == [original.id]
        assert delta["official_generation"] == "BLOCKED"

        assert any(
            Decimal(row["base_delta"]) != 0
            for row in delta["components"]
        )
        assert any(
            Decimal(row["tax_delta"]) != 0
            for row in delta["components"]
        )

        for row in delta["components"]:
            assert Decimal(row["base_delta"]) == (
                Decimal(row["replacement_base"])
                - Decimal(row["original_base"])
            )
            assert Decimal(row["tax_delta"]) == (
                Decimal(row["replacement_tax"])
                - Decimal(row["original_tax"])
            )
            assert row["original_base"] == row["original_sources"]
            assert row["replacement_base"] == row["replacement_sources"]

        active = await review_persisted_active_financials(
            db,
            company_id=company_id,
            payroll_period_id=period.id,
        )

        print("ACTIVE FINANCIAL STATUS =", active["status"])

        assert active["status"] == "ACTIVE_FINANCIAL_RECONCILED", active
        assert active["active_calculation_count"] == 1
        assert active["historical_calculation_count"] == 1
        assert active["active_base_total"] == (
            active["active_allocated_total"]
        )

        correction_count = await db.scalar(
            select(func.count(PayrollCorrection.id)).where(
                PayrollCorrection.company_id == company_id,
                PayrollCorrection.original_payroll_calculation_id
                == original.id,
                PayrollCorrection.replacement_payroll_calculation_id
                == replacement.id,
            )
        )

        assert correction_count == 1

        print("REAL REVISION REPLACEMENT = PASS")
        print("REAL DATED STATUTORY SOURCES = PASS")
        print("REAL FINANCIAL DELTA = PASS")
        print("ACTIVE REVISION ONLY = PASS")
        print("CORRECTION HISTORY PRESERVED = PASS")
        print("OFFICIAL GENERATION = BLOCKED")
        print("LARGE INTEGRATED CORRECTION ACCEPTANCE = PASS")

        await db.rollback()
