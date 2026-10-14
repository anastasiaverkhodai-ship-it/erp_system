"""Fail-closed payroll correction provenance integration.

Combines existing source evidence, salary-rate verification,
additional payroll derivation and coverage assessment.

This module does not authorize revision creation or execution.
The caller must hold the appropriate payroll mutation lock.
No commit, rollback or database mutations are performed here.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInput,
    PayrollInputSalarySlice,
)

from app.services.payroll_calculation_service import (
    validate_calculation_source,
)

from app.services.payroll_correction_source_evidence_service import (
    _fingerprint,
    _model_snapshot,
    capture_payroll_correction_source,
)

from app.services.payroll_correction_historical_salary_proof import (
    verify_historical_salary_sources,
)

from app.services.payroll_correction_additional_source_proof import (
    verify_additional_payroll_sources,
)

from app.services.payroll_correction_provenance_coverage import (
    assess_payroll_correction_source_coverage,
)


class PayrollProvenanceIntegrationError(ValueError):
    pass


def summarize_provenance_results(
    *,
    evidence: Mapping,
    salary_proof: Mapping,
    additional_proof: Mapping,
    coverage: Mapping,
    fingerprint_verified: bool,
    period_in_2026: bool,
    original_minimum_wage_assessment: object,
) -> dict:
    """Combine independently produced evidence without upgrading trust."""

    if not all(
        isinstance(item, Mapping)
        for item in (
            evidence,
            salary_proof,
            additional_proof,
            coverage,
        )
    ):
        raise PayrollProvenanceIntegrationError(
            "Invalid provenance verification result"
        )

    if fingerprint_verified is not True:
        raise PayrollProvenanceIntegrationError(
            "Payroll source fingerprint verification failed"
        )

    if (
        salary_proof.get(
            "historical_salary_rate_references_verified"
        ) is not True
    ):
        raise PayrollProvenanceIntegrationError(
            "Historical salary reference proof is incomplete"
        )

    if (
        additional_proof.get(
            "current_source_values_reconciled"
        ) is not True
    ):
        raise PayrollProvenanceIntegrationError(
            "Additional payroll source reconciliation is incomplete"
        )

    if not isinstance(
        evidence.get("source_fingerprint_sha256"), str
    ):
        raise PayrollProvenanceIntegrationError(
            "Payroll source fingerprint is missing"
        )

    blockers = set(coverage.get("blockers", []))

    if not isinstance(coverage.get("blockers"), list):
        raise PayrollProvenanceIntegrationError(
            "Provenance blockers must be a list"
        )

    if not period_in_2026:
        blockers.add("minimum_wage_period_outside_2026_rule")

    if not isinstance(
        original_minimum_wage_assessment, dict
    ):
        blockers.add("original_minimum_wage_assessment_missing")

    elif (
        original_minimum_wage_assessment.get("status")
        != "assessed"
    ):
        blockers.add("original_minimum_wage_assessment_not_assessed")

    blockers.update({
        "historical_source_immutability_unverified",
        "minimum_wage_original_derivation_unverified",
        "statutory_rule_and_context_provenance_unverified",
        "transactional_source_freeze_unverified",
    })

    return {
        "schema": "payroll-correction-provenance-integration-v1",
        "source_fingerprint_sha256": evidence[
            "source_fingerprint_sha256"
        ],
        "fingerprint_verified": True,
        "historical_salary_rate_references_verified": True,
        "additional_source_values_reconciled": True,
        "salary_proof": dict(salary_proof),
        "additional_source_proof": dict(additional_proof),
        "coverage": dict(coverage),
        "blockers": sorted(blockers),
        "source_evidence_complete": False,
        "automatic_revision_creation_allowed": False,
        "financial_execution_allowed": False,
    }


async def verify_payroll_correction_provenance(
    db,
    *,
    company_id: int,
    original_payroll_calculation_id: int,
    request_key: str,
    reason: str,
    expected_source_fingerprint_sha256: str,
) -> dict:
    """Cross-check independent evidence inside caller's transaction."""

    original = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id
            == original_payroll_calculation_id,
        )
    )

    if original is None:
        raise PayrollProvenanceIntegrationError(
            "Original payroll calculation not found"
        )

    evidence = await capture_payroll_correction_source(
        db,
        company_id=company_id,
        original_payroll_calculation_id=original.id,
        request_key=request_key,
        reason=reason,
    )

    fingerprint = evidence.get(
        "source_fingerprint_sha256"
    )

    if (
        not isinstance(
            expected_source_fingerprint_sha256, str
        )
        or fingerprint
        != expected_source_fingerprint_sha256
    ):
        raise PayrollProvenanceIntegrationError(
            "Payroll source changed since verification"
        )

    period, payroll_input, salary_slices = (
        await validate_calculation_source(
            db,
            company_id=company_id,
            payroll_input_id=original.payroll_input_id,
        )
    )

    if (
        payroll_input.id != original.payroll_input_id
        or period.id != original.payroll_period_id
        or payroll_input.employment_contract_id
        != original.employment_contract_id
    ):
        raise PayrollProvenanceIntegrationError(
            "Payroll source identity mismatch"
        )

    salary_proof = await verify_historical_salary_sources(
        db,
        company_id=company_id,
        employment_contract_id=original.employment_contract_id,
        payroll_input_id=payroll_input.id,
        payroll_calculation_id=original.id,
    )

    additional_proof = await verify_additional_payroll_sources(
        db,
        company_id=company_id,
        payroll_input=payroll_input,
        period=period,
        salary_slices=salary_slices,
        original_payroll_calculation_id=original.id,
    )

    original_lines = (
        await db.scalars(
            select(PayrollCalculationLine)
            .where(
                PayrollCalculationLine.company_id == company_id,
                PayrollCalculationLine.payroll_calculation_id
                == original.id,
            )
            .order_by(
                PayrollCalculationLine.line_no,
                PayrollCalculationLine.id,
            )
        )
    ).all()

    snapshot = {
        "schema": "payroll-correction-source-v1",
        "payroll_input": _model_snapshot(payroll_input),
        "salary_slices": [
            _model_snapshot(item)
            for item in salary_slices
        ],
        "calculation": _model_snapshot(original),
        "calculation_lines": [
            _model_snapshot(item)
            for item in original_lines
        ],
    }

    if _fingerprint(snapshot) != fingerprint:
        raise PayrollProvenanceIntegrationError(
            "Source snapshot differs from captured evidence"
        )

    coverage = assess_payroll_correction_source_coverage(
        snapshot
    )

    before_return = await capture_payroll_correction_source(
        db,
        company_id=company_id,
        original_payroll_calculation_id=original.id,
        request_key=request_key,
        reason=reason,
    )

    if (
        before_return.get("source_fingerprint_sha256")
        != fingerprint
        or before_return.get("next_revision")
        != evidence.get("next_revision")
    ):
        raise PayrollProvenanceIntegrationError(
            "Payroll source changed during provenance verification"
        )

    return summarize_provenance_results(
        evidence=evidence,
        salary_proof=salary_proof,
        additional_proof=additional_proof,
        coverage=coverage,
        fingerprint_verified=True,
        period_in_2026=(
            period.start_date.year == 2026
            and period.end_date.year == 2026
        ),
        original_minimum_wage_assessment=(
            original.minimum_wage_assessment
        ),
    )
