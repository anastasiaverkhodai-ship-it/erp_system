from decimal import Decimal
"""Real PostgreSQL: corrected payroll cannot bypass Order 100 classification."""

import os
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_correction_basis_postgresql import prepare, protect

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInput,
    PayrollPeriod,
)
from app.models.payroll_correction import PayrollCorrection
from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)
from app.services.payroll_correction_recalculation_service import (
    recalculate_minimum_wage_correction,
)
from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
)
from app.services.payroll_severance_database_average import (
    _validated_month,
)
from app.services.payroll_revision_service import (
    current_calculation_filter,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Requires disposable PostgreSQL copy",
    ),
]


async def test_real_correction_requires_severance_classification(
    employee_engine,
):
    await protect(employee_engine)

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        fixture, payroll_input, original, evidence, args = (
            await prepare(db)
        )

        company_id = fixture["company"]

        assert evidence["source_evidence_complete"] is True

        from app.services.payroll_correction_provenance_integration_service import (
            PayrollProvenanceIntegrationError,
            verify_payroll_correction_provenance,
        )

        integrated_proof = await verify_payroll_correction_provenance(
            db,
            company_id=company_id,
            original_payroll_calculation_id=original.id,
            request_key=args["request_key"],
            reason=args["reason"],
            expected_source_fingerprint_sha256=(
                evidence["source_fingerprint_sha256"]
            ),
        )

        assert isinstance(integrated_proof, dict)
        assert integrated_proof.get(
            "financial_execution_allowed"
        ) is not True
        assert integrated_proof.get(
            "automatic_revision_creation_allowed"
        ) is not True

        print("HISTORICAL INTEGRATED PROVENANCE = PASS")
        print("FINANCIAL AUTHORIZATION = NOT GRANTED")

        with pytest.raises(PayrollProvenanceIntegrationError):
            await verify_payroll_correction_provenance(
                db,
                company_id=company_id,
                original_payroll_calculation_id=original.id,
                request_key=args["request_key"],
                reason=args["reason"],
                expected_source_fingerprint_sha256="0" * 64,
            )

        print("ALTERED SOURCE FINGERPRINT = BLOCKED")

        assert original.revision == 1

        replacement, link = (
            await recalculate_minimum_wage_correction(
                db, **args
            )
        )

        assert replacement.revision == original.revision + 1

        from app.services.payroll_severance_minimum_wage_provenance import (
            SeveranceMinimumWageProvenanceError,
            inspect_severance_minimum_wage_source,
        )

        inspected = await inspect_severance_minimum_wage_source(
            db,
            company_id=company_id,
            payroll_calculation_id=replacement.id,
        )

        assert inspected.payroll_calculation_id == replacement.id
        assert inspected.revision == 2
        assert inspected.historical_salary_verified is True
        assert inspected.minimum_wage_rederived is True
        assert inspected.complete_payroll_provenance_verified is False
        assert inspected.financial_execution_allowed is False


        from app.services.payroll_severance_minimum_wage_provenance import (
            derive_verified_earnings_candidate,
        )

        candidate = await derive_verified_earnings_candidate(
            db,
            company_id=company_id,
            payroll_calculation_id=replacement.id,
        )

        assert candidate.revision == 2
        assert candidate.payroll_calculation_id == replacement.id
        assert candidate.minimum_wage_amount >= 0
        assert candidate.salary_amount > 0
        assert (
            candidate.salary_amount
            + candidate.minimum_wage_amount
            == candidate.gross_amount
            == replacement.gross_amount
        )
        assert candidate.order100_inclusion_authorized is False
        assert candidate.financial_execution_allowed is False

        # Classify the REAL corrected revision, not manually
        # fabricated salary or minimum-wage lines.
        from app.services.payroll_severance_order100_classifier import (
            Order100ClassificationError,
            require_supported_severance_lines,
        )

        current_lines = list((
            await db.scalars(
                select(PayrollCalculationLine).where(
                    PayrollCalculationLine.company_id == company_id,
                    PayrollCalculationLine.payroll_calculation_id
                    == replacement.id,
                )
            )
        ).all())

        assert current_lines
        assert candidate.minimum_wage_amount > 0

        # The default API must remain fail-closed.
        with pytest.raises(
            Order100ClassificationError,
            match="Non-salary earnings require",
        ):
            require_supported_severance_lines(
                current_lines,
                gross_amount=candidate.gross_amount,
            )

        # The genuine historical payroll proof supplies the
        # exact classified minimum-wage amount.
        eligible = require_supported_severance_lines(
            current_lines,
            gross_amount=candidate.gross_amount,
            verified_minimum_wage_amount=(
                candidate.minimum_wage_amount
            ),
        )

        assert eligible == replacement.gross_amount
        assert eligible == (
            candidate.salary_amount
            + candidate.minimum_wage_amount
        )

        # Altering the proven top-up must fail.
        with pytest.raises(
            Order100ClassificationError,
            match="Verified minimum-wage amount differs",
        ):
            require_supported_severance_lines(
                current_lines,
                gross_amount=candidate.gross_amount,
                verified_minimum_wage_amount=(
                    candidate.minimum_wage_amount
                    + Decimal("0.01")
                ),
            )

        # Altering the total gross must also fail.
        with pytest.raises(Order100ClassificationError):
            require_supported_severance_lines(
                current_lines,
                gross_amount=(
                    candidate.gross_amount + Decimal("0.01")
                ),
                verified_minimum_wage_amount=(
                    candidate.minimum_wage_amount
                ),
            )

        assert candidate.order100_inclusion_authorized is False
        assert candidate.financial_execution_allowed is False

        print("REAL CORRECTED ORDER100 CLASSIFICATION = PASS")

        # This eligibility proof uses the actual corrected
        # PostgreSQL calculation, its stored assessment,
        # and its current calculation lines.
        from dataclasses import replace

        from app.services.payroll_severance_order100_minimum_eligibility import (
            Order100MinimumEligibilityError,
            authorize_verified_minimum_wage_for_average,
        )

        actual_period = await db.scalar(
            select(PayrollPeriod).where(
                PayrollPeriod.company_id == company_id,
                PayrollPeriod.id == replacement.payroll_period_id,
            )
        )
        assert actual_period is not None

        legal_decision = authorize_verified_minimum_wage_for_average(
            candidate=candidate,
            calculation=replacement,
            period=actual_period,
            lines=current_lines,
        )

        assert legal_decision.eligible_for_average is True
        assert legal_decision.financial_execution_allowed is False
        assert legal_decision.payroll_calculation_id == replacement.id
        assert legal_decision.revision == replacement.revision == 2
        assert (
            legal_decision.minimum_wage_amount
            == candidate.minimum_wage_amount
            > 0
        )
        assert candidate.order100_inclusion_authorized is False

        # An altered candidate must not inherit eligibility.
        altered_candidate = replace(
            candidate,
            minimum_wage_amount=(
                candidate.minimum_wage_amount + Decimal("0.01")
            ),
        )

        with pytest.raises(Order100MinimumEligibilityError):
            authorize_verified_minimum_wage_for_average(
                candidate=altered_candidate,
                calculation=replacement,
                period=actual_period,
                lines=current_lines,
            )

        stale_candidate = replace(candidate, revision=1)

        with pytest.raises(Order100MinimumEligibilityError):
            authorize_verified_minimum_wage_for_average(
                candidate=stale_candidate,
                calculation=replacement,
                period=actual_period,
                lines=current_lines,
            )

        print("REAL ORDER100 ELIGIBILITY POSTGRESQL = PASS")
        print("CURRENT PAYROLL REVISION ELIGIBILITY = PASS")
        print("ALTERED ELIGIBILITY CANDIDATE = BLOCKED")
        print("STALE ELIGIBILITY CANDIDATE = BLOCKED")
        print("ELIGIBILITY DOES NOT AUTHORIZE PAYMENT = PASS")
        print("GENUINE PAYROLL AMOUNT MATCH = PASS")
        print("UNVERIFIED DEFAULT CLASSIFICATION = BLOCKED")
        print("ALTERED MINIMUM WAGE PROOF = BLOCKED")
        print("ALTERED PAYROLL GROSS = BLOCKED")
        print("FINANCIAL AUTHORIZATION = NOT GRANTED")

        print("REAL EARNINGS CANDIDATE = PASS")
        print("SALARY AND MINIMUM WAGE RECONCILIATION = PASS")
        print("ORDER100 FINANCIAL USE = BLOCKED")

        print("CURRENT MINIMUM WAGE REVISION = VERIFIED")
        print("HISTORICAL SALARY AND REDERIVATION = PASS")
        print("FINANCIAL EXECUTION = BLOCKED")

        with pytest.raises(
            SeveranceMinimumWageProvenanceError,
            match="Current company-scoped calculation required",
        ):
            await inspect_severance_minimum_wage_source(
                db,
                company_id=company_id,
                payroll_calculation_id=original.id,
            )

        print("STALE MINIMUM WAGE REVISION = BLOCKED")

        assert replacement.id != original.id
        assert link.original_payroll_calculation_id == original.id
        assert link.replacement_payroll_calculation_id == replacement.id
        assert replacement.gross_amount != original.gross_amount

        current = list((
            await db.scalars(
                select(PayrollCalculation).where(
                    PayrollCalculation.company_id == company_id,
                    PayrollCalculation.payroll_input_id
                    == payroll_input.id,
                    current_calculation_filter(),
                )
            )
        ).all())

        assert len(current) == 1
        assert current[0].id == replacement.id

        lines = list((
            await db.scalars(
                select(PayrollCalculationLine).where(
                    PayrollCalculationLine.company_id == company_id,
                    PayrollCalculationLine.payroll_calculation_id
                    == replacement.id,
                )
            )
        ).all())

        assert lines

        non_salary = [
            line for line in lines
            if getattr(line.line_type, "value", line.line_type)
            != "salary"
        ]

        assert non_salary, (
            "Fixture no longer contains the non-salary component "
            "required to test fail-closed classification"
        )

        period = await db.get(
            PayrollPeriod,
            replacement.payroll_period_id,
        )

        saved_input = await db.get(
            PayrollInput,
            replacement.payroll_input_id,
        )

        assert period is not None
        assert saved_input is not None

        from copy import deepcopy
        from app.services.payroll_correction_minimum_wage_proof import (
            PayrollMinimumWageProofError,
            verify_saved_minimum_wage_assessment,
        )

        salary_types = {
            getattr(item.salary_rate_type, "value",
                    item.salary_rate_type)
            for item in lines
            if item.line_type == "salary"
            and item.salary_rate_type is not None
        }

        assert len(salary_types) == 1, (
            "Cannot verify minimum wage without a unique "
            "historical salary rate type"
        )

        salary_type = next(iter(salary_types))
        saved_assessment = replacement.minimum_wage_assessment

        proof = verify_saved_minimum_wage_assessment(
            assessment=saved_assessment,
            calculation_lines=lines,
            period_start=period.start_date,
            period_end=period.end_date,
            expected_salary_rate_type=salary_type,
        )

        assert proof["saved_assessment_consistent"] is True
        assert proof["historical_earnings_provenance_verified"] is False
        assert proof["complete_payroll_provenance_verified"] is False
        assert proof["financial_execution_allowed"] is False

        tampered_assessment = deepcopy(saved_assessment)
        tampered_assessment["top_up"] = str(
            Decimal(str(saved_assessment["top_up"]))
            + Decimal("1.00")
        )

        with pytest.raises(PayrollMinimumWageProofError):
            verify_saved_minimum_wage_assessment(
                assessment=tampered_assessment,
                calculation_lines=lines,
                period_start=period.start_date,
                period_end=period.end_date,
                expected_salary_rate_type=salary_type,
            )

        print("MINIMUM WAGE SAVED ASSESSMENT = PASS")
        print("TAMPERED MINIMUM WAGE ASSESSMENT = BLOCKED")
        print("UNVERIFIED HISTORICAL PROVENANCE = BLOCKED")


        before = list((
            await db.scalars(
                select(PayrollTerminationSeverance.id).where(
                    PayrollTerminationSeverance.company_id
                    == company_id,
                    PayrollTerminationSeverance.employment_contract_id
                    == original.employment_contract_id,
                )
            )
        ).all())

        with pytest.raises(
            SeveranceAverageError,
            match="Non-salary earnings require",
        ):
            _validated_month(
                period=period,
                calculation=replacement,
                payroll_input=saved_input,
                lines=lines,
                attendance=[],
                schedule_minutes={},
                month=period.start_date,
                month_end=period.end_date,
            )

        after = list((
            await db.scalars(
                select(PayrollTerminationSeverance.id).where(
                    PayrollTerminationSeverance.company_id
                    == company_id,
                    PayrollTerminationSeverance.employment_contract_id
                    == original.employment_contract_id,
                )
            )
        ).all())

        assert before == after

        print("REAL PAYROLL CORRECTION = PASS")
        print("CURRENT PAYROLL REVISION = PASS")
        print("CORRECTED PAYROLL GROSS CHANGED = PASS")
        print("NON-SALARY CLASSIFICATION = BLOCKED")
        print("SEVERANCE SOURCE ZERO-LEAK = PASS")

        await db.rollback()
