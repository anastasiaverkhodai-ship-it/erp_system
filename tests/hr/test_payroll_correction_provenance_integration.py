"""Fail-closed provenance integration contract tests."""

from copy import deepcopy

import pytest

from app.services.payroll_correction_provenance_integration_service import (
    PayrollProvenanceIntegrationError,
    summarize_provenance_results,
)


def _proofs():
    return {
        "evidence": {
            "source_fingerprint_sha256": "a" * 64,
            "source_evidence_complete": False,
        },
        "salary_proof": {
            "historical_salary_rate_references_verified": True,
            "complete_payroll_provenance_verified": False,
        },
        "additional_proof": {
            "current_source_values_reconciled": True,
            "historical_source_immutability_verified": False,
        },
        "coverage": {
            "blockers": [
                "minimum_wage_statutory_source_unverified"
            ],
            "coverage_complete": False,
        },
        "fingerprint_verified": True,
        "period_in_2026": True,
        "original_minimum_wage_assessment": {
            "status": "assessed",
            "rule_version": "UA-4695-IX-2026-v1",
        },
    }


def test_complete_current_consistency_remains_fail_closed():
    result = summarize_provenance_results(**_proofs())

    assert result["fingerprint_verified"] is True
    assert (
        result["historical_salary_rate_references_verified"]
        is True
    )
    assert result["additional_source_values_reconciled"] is True

    assert result["source_evidence_complete"] is False
    assert result["automatic_revision_creation_allowed"] is False
    assert result["financial_execution_allowed"] is False

    assert (
        "historical_source_immutability_unverified"
        in result["blockers"]
    )


def test_stale_source_fingerprint_rejected():
    proofs = _proofs()
    proofs["fingerprint_verified"] = False

    with pytest.raises(
        PayrollProvenanceIntegrationError,
        match="fingerprint",
    ):
        summarize_provenance_results(**proofs)


def test_missing_salary_proof_rejected():
    proofs = _proofs()
    proofs["salary_proof"][
        "historical_salary_rate_references_verified"
    ] = False

    with pytest.raises(
        PayrollProvenanceIntegrationError,
        match="Historical salary",
    ):
        summarize_provenance_results(**proofs)


def test_missing_additional_source_proof_rejected():
    proofs = _proofs()
    proofs["additional_proof"][
        "current_source_values_reconciled"
    ] = False

    with pytest.raises(
        PayrollProvenanceIntegrationError,
        match="Additional payroll",
    ):
        summarize_provenance_results(**proofs)


def test_out_of_period_rule_remains_blocked():
    proofs = _proofs()
    proofs["period_in_2026"] = False

    result = summarize_provenance_results(**proofs)

    assert (
        "minimum_wage_period_outside_2026_rule"
        in result["blockers"]
    )

    assert result["source_evidence_complete"] is False


def test_missing_original_assessment_remains_blocked():
    proofs = _proofs()
    proofs["original_minimum_wage_assessment"] = None

    result = summarize_provenance_results(**proofs)

    assert (
        "original_minimum_wage_assessment_missing"
        in result["blockers"]
    )

    assert result["automatic_revision_creation_allowed"] is False


def test_non_assessed_original_remains_blocked():
    proofs = _proofs()

    proofs["original_minimum_wage_assessment"] = {
        "status": "requires_uah_conversion"
    }

    result = summarize_provenance_results(**proofs)

    assert (
        "original_minimum_wage_assessment_not_assessed"
        in result["blockers"]
    )


def test_results_are_deterministic_and_inputs_immutable():
    proofs = _proofs()
    before = deepcopy(proofs)

    first = summarize_provenance_results(**proofs)
    second = summarize_provenance_results(**proofs)

    assert first == second
    assert proofs == before
