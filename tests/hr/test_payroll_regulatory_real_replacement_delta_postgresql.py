"""Real two-revision statutory financial correction acceptance.

No fabricated calculation, correction, tax or allocation records.
All financial evidence must originate in existing ERP services.
Use the dated writer: the legacy aggregate calculation does not promise
source-level allocations for regulatory reconciliation.
"""

from decimal import Decimal
import os

import pytest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine

from test_payroll_correction_basis_postgresql import (
    prepare,
    protect,
)

from app.models.payroll import PayrollPeriod
from app.models.payroll_correction import PayrollCorrection
from app.models.payroll_statutory import (
    PayrollStatutoryComponent,
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
    PayrollStatutoryLineSource,
)

from app.services.payroll_statutory_service import (
    create_statutory_rate,
)

from app.services.payroll_dated_employee_statutory import (
    write_dated_employee_statutory_result,
)

from app.services.payroll_correction_source_evidence_service import (
    capture_payroll_correction_source,
)

from app.services.payroll_correction_recalculation_service import (
    recalculate_minimum_wage_correction,
)

from app.services.payroll_regulatory_persisted_correction_delta import (
    review_persisted_correction_delta,
)

from app.services.payroll_regulatory_revision_chain_review import (
    review_persisted_revision_chain,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Isolated PostgreSQL required",
    ),
]


async def test_real_replacement_statutory_delta_positive(employee_engine):
    await protect(employee_engine)

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        fixture, payroll_input, original, evidence, args = (
            await prepare(db)
        )

        company_id = fixture["company"]
        actor_id = fixture["actor"]

        period = await db.get(
            PayrollPeriod,
            original.payroll_period_id,
        )

        assert period is not None
        assert original.revision == 1
        assert original.gross_amount == Decimal("4323.50")

        for component, rate in (
            (
                PayrollStatutoryComponent.PERSONAL_INCOME_TAX,
                Decimal("0.18"),
            ),
            (
                PayrollStatutoryComponent.MILITARY_LEVY,
                Decimal("0.05"),
            ),
            (
                PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION,
                Decimal("0.22"),
            ),
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

        original_statutory = await write_dated_employee_statutory_result(
            db,
            company_id=company_id,
            payroll_calculation_id=original.id,
            actor_user_id=actor_id,
        )

        assert original_statutory.id is not None

        original_source_count = await db.scalar(
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
                == original_statutory.id,
            )
        )

        print("ORIGINAL DATED SOURCES =", original_source_count)

        assert original_source_count > 0, (
            "Original calculation has no authentic attributed sources"
        )

        verified_source = await capture_payroll_correction_source(
            db,
            company_id=company_id,
            original_payroll_calculation_id=original.id,
            request_key=args["request_key"],
            reason=args["reason"],
        )

        assert verified_source["source_evidence_complete"] is True

        correction_args = dict(args)
        correction_args["expected_source_fingerprint_sha256"] = (
            verified_source["source_fingerprint_sha256"]
        )

        replacement, correction = (
            await recalculate_minimum_wage_correction(
                db,
                **correction_args,
            )
        )

        assert replacement.id != original.id
        assert replacement.revision == original.revision + 1
        assert replacement.payroll_input_id == original.payroll_input_id
        assert replacement.employment_contract_id == (
            original.employment_contract_id
        )

        assert replacement.gross_amount == Decimal("4755.85")

        gross_delta = (
            replacement.gross_amount - original.gross_amount
        )

        assert gross_delta == Decimal("432.35")

        assert correction.original_payroll_calculation_id == original.id
        assert correction.replacement_payroll_calculation_id == (
            replacement.id
        )

        replacement_statutory = (
            await write_dated_employee_statutory_result(
                db,
                company_id=company_id,
                payroll_calculation_id=replacement.id,
                actor_user_id=actor_id,
            )
        )

        assert replacement_statutory.id != original_statutory.id

        replacement_source_count = await db.scalar(
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
                == replacement_statutory.id,
            )
        )

        print(
            "REPLACEMENT DATED SOURCES =",
            replacement_source_count,
        )

        assert replacement_source_count > 0, (
            "Replacement calculation has no authentic attributed sources"
        )

        chain = await review_persisted_revision_chain(
            db,
            company_id=company_id,
            payroll_period_id=original.payroll_period_id,
        )

        print("REAL REVISION CHAIN =", chain["status"])

        assert chain["status"] == "REVISION_CHAIN_VERIFIED", chain
        assert chain["active_calculation_ids"] == [replacement.id]
        assert chain["historical_calculation_ids"] == [original.id]
        assert chain["correction_count"] == 1

        result = await review_persisted_correction_delta(
            db,
            company_id=company_id,
            payroll_period_id=original.payroll_period_id,
        )

        print("REAL DELTA STATUS =", result["status"])
        print(
            "REAL PERSISTED DELTA VERIFIED =",
            result.get("persisted_correction_delta_verified"),
        )
        print("REAL DELTA COMPONENTS =", result.get("components"))
        print("REAL ORIGINAL GROSS =", original.gross_amount)
        print("REAL REPLACEMENT GROSS =", replacement.gross_amount)
        print("REAL GROSS DELTA =", gross_delta)

        assert result["status"] == (
            "CORRECTION_COMPONENT_DELTA_RECONCILED"
        ), result

        assert result["persisted_correction_delta_verified"] is True
        assert result["data_origin"] == "PERSISTED_POSTGRESQL"
        assert result["correction_chain_provenance"] == (
            "PERSISTED_POSTGRESQL"
        )

        assert result["active_calculation_ids"] == [replacement.id]
        assert result["historical_calculation_ids"] == [original.id]

        assert result["missing_source_line_ids"] == []
        assert result["mismatched_source_line_ids"] == []

        assert result["historical_double_count_in_active_totals"] is False
        assert result["official_generation"] == "BLOCKED"
        assert result["statutory_mapping_verified"] is False

        assert result["components"], (
            "No financial tax components were reconciled"
        )

        # Require an actual, nonzero financial change in the
        # persisted statutory evidence, not only a revision link.
        assert any(
            Decimal(item["base_delta"]) != Decimal("0.00")
            for item in result["components"]
        ), result

        assert any(
            Decimal(item["tax_delta"]) != Decimal("0.00")
            for item in result["components"]
        ), result

        for item in result["components"]:
            assert Decimal(item["base_delta"]) == (
                Decimal(item["replacement_base"])
                - Decimal(item["original_base"])
            )

            assert Decimal(item["tax_delta"]) == (
                Decimal(item["replacement_tax"])
                - Decimal(item["original_tax"])
            )

            assert Decimal(item["original_sources"]) == (
                Decimal(item["original_base"])
            )

            assert Decimal(item["replacement_sources"]) == (
                Decimal(item["replacement_base"])
            )

        persisted_link_count = await db.scalar(
            select(func.count(PayrollCorrection.id)).where(
                PayrollCorrection.company_id == company_id,
                PayrollCorrection.original_payroll_calculation_id
                == original.id,
                PayrollCorrection.replacement_payroll_calculation_id
                == replacement.id,
            )
        )

        assert persisted_link_count == 1

        print("REAL REPLACEMENT FINANCIAL DELTA E2E = PASS")
        print("HISTORICAL DOUBLE COUNT CONTROL = PASS")
        print("AUTHENTIC STATUTORY SOURCES = PASS")
        print("OFFICIAL GENERATION = BLOCKED")

        await db.rollback()
