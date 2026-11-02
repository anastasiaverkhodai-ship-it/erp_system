"""Strict acceptance rules for persisted financial reconciliation."""

from copy import deepcopy

import pytest

from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)

from app.services.payroll_regulatory_persisted_allocation import (
    verify_allocations,
)


def evidence():
    return (
        [
            {
                "id": 101,
                "company_id": 1,
                "payroll_period_id": 2,
                "reported_amount": "1250.00",
            },
            {
                "id": 102,
                "company_id": 1,
                "payroll_period_id": 2,
                "reported_amount": "175.25",
            },
        ],
        [
            {
                "id": 201,
                "statutory_line_id": 101,
                "payroll_calculation_line_id": 301,
                "company_id": 1,
                "source_amount": "1000.00",
            },
            {
                "id": 202,
                "statutory_line_id": 101,
                "payroll_calculation_line_id": 302,
                "company_id": 1,
                "source_amount": "250.00",
            },
            {
                "id": 203,
                "statutory_line_id": 102,
                "payroll_calculation_line_id": 302,
                "company_id": 1,
                "source_amount": "175.25",
            },
        ],
        [
            {
                "id": 301,
                "company_id": 1,
                "payroll_period_id": 2,
            },
            {
                "id": 302,
                "company_id": 1,
                "payroll_period_id": 2,
            },
        ],
    )


def run(parts):
    return verify_allocations(
        *parts,
        company_id=1,
        payroll_period_id=2,
    )


def test_positive_complete_allocation():
    result = run(evidence())

    assert result["status"] == "FINANCIAL_LINES_RECONCILED"
    assert result["line_count"] == 2
    assert result["verified_lines"] == 2
    assert result["reported_total"] == "1425.25"
    assert result["allocated_total"] == "1425.25"
    assert result["statutory_mapping_verified"] is False
    assert result["official_generation"] == "BLOCKED"


def test_changed_source_amount_is_not_verified():
    parts = evidence()
    parts[1][1]["source_amount"] = "249.99"

    result = run(parts)

    assert result["status"] == "NOT_VERIFIED"
    assert result["verified_lines"] == 1
    assert result["lines"][0]["difference"] == "0.01"


def test_missing_source_is_not_verified():
    parts = evidence()
    parts[1].pop()

    result = run(parts)

    assert result["status"] == "NOT_VERIFIED"
    assert result["verified_lines"] == 1


def test_missing_all_report_lines_is_not_verified():
    assert run(([], [], []))["status"] == "NOT_VERIFIED"


@pytest.mark.parametrize(
    "change",
    (
        "duplicate_source",
        "duplicate_line",
        "wrong_company",
        "wrong_period",
        "missing_payroll_calculation",
        "orphan_statutory_source",
    ),
)
def test_invalid_source_evidence_is_rejected(change):
    parts = evidence()

    if change == "duplicate_source":
        parts[1].append(deepcopy(parts[1][0]))
    elif change == "duplicate_line":
        parts[0].append(deepcopy(parts[0][0]))
    elif change == "wrong_company":
        parts[1][0]["company_id"] = 999
    elif change == "wrong_period":
        parts[2][0]["payroll_period_id"] = 999
    elif change == "missing_payroll_calculation":
        parts[2].pop(0)
    elif change == "orphan_statutory_source":
        parts[1][0]["statutory_line_id"] = 999

    with pytest.raises(FinancialReconciliationError):
        run(parts)
