"""Source coverage contract tests."""

from copy import deepcopy

import pytest

from app.services.payroll_correction_provenance_coverage import (
    PayrollProvenanceCoverageError,
    assess_payroll_correction_source_coverage,
)


def _snapshot():
    return {
        "schema": "payroll-correction-source-v1",
        "payroll_input": {"id": 1},
        "salary_slices": [
            {
                "id": 10,
                "salary_rate_id": 20,
                "currency_code": "UAH",
            }
        ],
        "calculation": {
            "id": 30,
            "currency_code": "UAH",
            "gross_amount": "10000.00",
        },
        "calculation_lines": [
            {
                "id": 40,
                "line_type": "salary",
                "source_salary_rate_id": 20,
                "currency_code": "UAH",
                "amount": "10000.00",
            }
        ],
    }


def test_valid_basic_snapshot_is_still_fail_closed():
    result = assess_payroll_correction_source_coverage(
        _snapshot()
    )

    assert result["salary_slice_count"] == 1
    assert result["calculation_line_count"] == 1
    assert result["blocker_count"] >= 6
    assert result["coverage_complete"] is False
    assert result["automatic_revision_creation_allowed"] is False
    assert result["financial_execution_allowed"] is False


def test_missing_salary_slices_are_reported():
    snapshot = _snapshot()
    snapshot["salary_slices"] = []

    result = assess_payroll_correction_source_coverage(
        snapshot
    )

    assert "missing_salary_slices" in result["blockers"]


def test_salary_rate_reference_mismatch_is_reported():
    snapshot = _snapshot()
    snapshot["calculation_lines"][0][
        "source_salary_rate_id"
    ] = 999

    result = assess_payroll_correction_source_coverage(
        snapshot
    )

    assert (
        "salary_line_rate_not_in_input_slices"
        in result["blockers"]
    )


def test_currency_mismatch_is_reported():
    snapshot = _snapshot()
    snapshot["salary_slices"][0]["currency_code"] = "EUR"

    result = assess_payroll_correction_source_coverage(
        snapshot
    )

    assert (
        "salary_slice_currency_mismatch"
        in result["blockers"]
    )


def test_gross_mismatch_is_reported():
    snapshot = _snapshot()
    snapshot["calculation"]["gross_amount"] = "9999.00"

    result = assess_payroll_correction_source_coverage(
        snapshot
    )

    assert (
        "calculation_gross_mismatch"
        in result["blockers"]
    )


def test_additional_component_requires_proof():
    snapshot = _snapshot()

    snapshot["calculation_lines"].append({
        "line_type": "vacation",
        "currency_code": "UAH",
        "amount": "100.00",
    })

    snapshot["calculation"]["gross_amount"] = "10100.00"

    result = assess_payroll_correction_source_coverage(
        snapshot
    )

    assert (
        "additional_calculation_component_requires_proof"
        in result["blockers"]
    )


def test_unsupported_schema_rejected():
    snapshot = _snapshot()
    snapshot["schema"] = "unsupported"

    with pytest.raises(
        PayrollProvenanceCoverageError
    ):
        assess_payroll_correction_source_coverage(snapshot)


def test_nonfinite_amount_rejected():
    snapshot = _snapshot()
    snapshot["calculation_lines"][0]["amount"] = "NaN"

    with pytest.raises(
        PayrollProvenanceCoverageError
    ):
        assess_payroll_correction_source_coverage(snapshot)


def test_repeat_is_deterministic_and_does_not_mutate():
    snapshot = _snapshot()
    before = deepcopy(snapshot)

    first = assess_payroll_correction_source_coverage(
        snapshot
    )
    second = assess_payroll_correction_source_coverage(
        snapshot
    )

    assert first == second
    assert snapshot == before
