"""Saved minimum-wage assessment integrity tests."""

from copy import deepcopy
from datetime import date

import pytest

from app.services.payroll_correction_minimum_wage_proof import (
    PayrollMinimumWageProofError,
    verify_saved_minimum_wage_assessment,
)


START = date(2026, 4, 1)
END = date(2026, 4, 30)


def _case():
    assessment = {
        "status": "assessed",
        "rule_version": "UA-4695-IX-2026-v1",
        "source_reference":
            "https://zakon.rada.gov.ua/laws/show/4695-IX",
        "rate_type": "hourly",
        "minimum_rate": "52",
        "ordinary_worked_minutes": 6000,
        "full_time_norm_minutes": None,
        "guaranteed_amount": "5200.00",
        "included_earnings": "5000.00",
        "excluded_earnings": "0.00",
        "top_up": "200.00",
        "request": {},
    }

    lines = [{
        "line_type": "minimum_wage",
        "currency_code": "UAH",
        "quantity": 1,
        "rate": "200.00",
        "amount": "200.00",
        "source_effective_from": START,
        "source_effective_to": END,
    }]

    return assessment, lines


def _verify(assessment, lines, **changes):
    arguments = {
        "assessment": assessment,
        "calculation_lines": lines,
        "period_start": START,
        "period_end": END,
        "expected_salary_rate_type": "hourly",
    }
    arguments.update(changes)
    return verify_saved_minimum_wage_assessment(**arguments)


def test_valid_assessment_remains_fail_closed():
    assessment, lines = _case()
    result = _verify(assessment, lines)

    assert result["saved_assessment_consistent"] is True
    assert result["complete_payroll_provenance_verified"] is False
    assert result["automatic_revision_creation_allowed"] is False
    assert result["financial_execution_allowed"] is False


def test_wrong_rule_version_rejected():
    assessment, lines = _case()
    assessment["rule_version"] = "unknown"

    with pytest.raises(PayrollMinimumWageProofError):
        _verify(assessment, lines)


def test_wrong_minimum_rate_rejected():
    assessment, lines = _case()
    assessment["minimum_rate"] = "40"

    with pytest.raises(PayrollMinimumWageProofError):
        _verify(assessment, lines)


def test_wrong_top_up_rejected():
    assessment, lines = _case()
    assessment["top_up"] = "150"

    with pytest.raises(PayrollMinimumWageProofError):
        _verify(assessment, lines)


def test_wrong_calculation_line_rejected():
    assessment, lines = _case()
    lines[0]["amount"] = "201"

    with pytest.raises(PayrollMinimumWageProofError):
        _verify(assessment, lines)


def test_wrong_effective_dates_rejected():
    assessment, lines = _case()
    lines[0]["source_effective_to"] = date(2026, 5, 1)

    with pytest.raises(PayrollMinimumWageProofError):
        _verify(assessment, lines)


def test_2096_period_rejected():
    assessment, lines = _case()

    with pytest.raises(PayrollMinimumWageProofError):
        _verify(
            assessment,
            lines,
            period_start=date(2096, 4, 1),
            period_end=date(2096, 4, 30),
        )


def test_nonfinite_amount_rejected():
    assessment, lines = _case()
    assessment["top_up"] = "NaN"

    with pytest.raises(PayrollMinimumWageProofError):
        _verify(assessment, lines)


def test_zero_top_up_requires_no_line():
    assessment, lines = _case()
    assessment["included_earnings"] = "5200.00"
    assessment["top_up"] = "0.00"

    result = _verify(assessment, [])

    assert result["minimum_wage_line_count"] == 0
    assert result["automatic_revision_creation_allowed"] is False


def test_deterministic_and_no_input_mutation():
    assessment, lines = _case()
    before = deepcopy((assessment, lines))

    assert _verify(assessment, lines) == _verify(
        assessment, lines
    )
    assert (assessment, lines) == before
