from copy import deepcopy

import pytest

from app.services.payroll_regulatory_d1_completeness import (
    D1CompletenessError,
    strict_d1_completeness,
)


def valid_review():
    return {
        "company_id": 1,
        "payroll_period_id": 2,
        "report_kind": "d1",
        "review_only": True,
        "official_statutory_generation": "BLOCKED",
        "d1_financial_reconciliation": "PASS",
        "dated_statutory_line_count": 1,
        "missing_dated_source_count": 0,
        "dated_provenance_status": "COMPLETE",
        "employer_contribution_total": "22.00",
        "dated_controls": [{
            "statutory_line_id": 11,
            "payroll_calculation_id": 7,
            "statutory_contribution_amount": "22.00",
            "dated_source_count": 1,
            "dated_source_amount_total": "100.00",
            "dated_source_coverage": "present",
            "dated_sources": [{
                "source_id": 13,
                "payroll_calculation_line_id": 20,
                "income_effective_from": "2026-10-01",
                "income_effective_to": "2026-10-31",
                "source_amount": "100.00",
            }],
        }],
    }


def test_complete_source_coverage():
    result = strict_d1_completeness(valid_review())
    assert result["source_integrity"] == "PASS"
    assert result["d1_status"] == "SOURCE_COVERAGE_PRESENT"
    assert result["statutory_base_allocation_verified"] is False
    assert result["dated_source_count"] == 1


def test_missing_source_is_explicit_not_approved():
    review = valid_review()
    control = review["dated_controls"][0]
    control["dated_sources"] = []
    control["dated_source_count"] = 0
    control["dated_source_amount_total"] = "0.00"
    control["dated_source_coverage"] = "missing"
    review["missing_dated_source_count"] = 1
    review["dated_provenance_status"] = "INCOMPLETE"

    result = strict_d1_completeness(review)
    assert result["d1_status"] == "SOURCE_COVERAGE_INCOMPLETE"
    assert result["missing_source_line_ids"] == [11]


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "wrong_amount",
        "wrong_total",
        "wrong_count",
        "wrong_status",
        "wrong_scope",
        "wrong_dates",
        "unblocked_generation",
    ],
)
def test_integrity_violations_rejected(mutation):
    review = deepcopy(valid_review())
    control = review["dated_controls"][0]

    if mutation == "duplicate":
        review["dated_controls"].append(deepcopy(control))
        review["dated_statutory_line_count"] = 2
        review["employer_contribution_total"] = "44.00"

    elif mutation == "wrong_amount":
        control["dated_sources"][0]["source_amount"] = "99.00"

    elif mutation == "wrong_total":
        review["employer_contribution_total"] = "23.00"

    elif mutation == "wrong_count":
        control["dated_source_count"] = 2

    elif mutation == "wrong_status":
        review["dated_provenance_status"] = "INCOMPLETE"

    elif mutation == "wrong_scope":
        review["company_id"] = -1

    elif mutation == "wrong_dates":
        control["dated_sources"][0][
            "income_effective_from"
        ] = "2026-11-01"

    elif mutation == "unblocked_generation":
        review["official_statutory_generation"] = "READY"

    with pytest.raises(D1CompletenessError):
        strict_d1_completeness(review)


def test_unsupported_official_claim_rejected():
    review = valid_review()
    review["review_only"] = False

    with pytest.raises(D1CompletenessError):
        strict_d1_completeness(review)
