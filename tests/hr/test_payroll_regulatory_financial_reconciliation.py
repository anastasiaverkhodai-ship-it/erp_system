from copy import deepcopy

import pytest

from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
    money,
    reconcile_lines,
)


def evidence():
    return [{
        "line_id": 11,
        "company_id": 1,
        "payroll_period_id": 2,
        "reported_amount": "100.00",
        "sources": [
            {"source_id": 21, "source_amount": "60.00"},
            {"source_id": 22, "source_amount": "40.00"},
        ],
    }]


def calculate(lines):
    return reconcile_lines(
        lines,
        company_id=1,
        payroll_period_id=2,
    )


def test_independently_balanced_sources():
    result = calculate(evidence())
    assert result["verified_line_count"] == 1
    assert result["reported_total"] == "100.00"
    assert result["source_total"] == "100.00"
    assert result["status"] == "FINANCIAL_LINES_RECONCILED"
    assert result["official_generation"] == "BLOCKED"


def test_discrepancy_detected():
    lines = evidence()
    lines[0]["sources"][1]["source_amount"] = "39.99"
    result = calculate(lines)
    assert result["blocked_line_count"] == 1
    assert result["differences"][0]["difference"] == "0.01"
    assert result["status"] == "NOT_VERIFIED"


def test_no_sources_fail_closed():
    lines = evidence()
    lines[0]["sources"] = []
    assert calculate(lines)["status"] == "NOT_VERIFIED"


def test_no_lines_fail_closed():
    assert calculate([])["status"] == "NOT_VERIFIED"


@pytest.mark.parametrize(
    "invalid",
    [True, False, 1.2, None, "NaN", "Infinity", "12.001"],
)
def test_invalid_money_rejected(invalid):
    with pytest.raises(FinancialReconciliationError):
        money(invalid)


@pytest.mark.parametrize(
    "mutation",
    [
        "company",
        "period",
        "duplicate_line",
        "duplicate_source",
        "negative_amount",
        "negative_source",
    ],
)
def test_invalid_evidence_rejected(mutation):
    lines = evidence()

    if mutation == "company":
        lines[0]["company_id"] = 99
    elif mutation == "period":
        lines[0]["payroll_period_id"] = 99
    elif mutation == "duplicate_line":
        lines.append(deepcopy(lines[0]))
    elif mutation == "duplicate_source":
        lines[0]["sources"].append(
            deepcopy(lines[0]["sources"][0])
        )
    elif mutation == "negative_amount":
        lines[0]["reported_amount"] = "-1.00"
    elif mutation == "negative_source":
        lines[0]["sources"][0]["source_amount"] = "-1.00"

    with pytest.raises(FinancialReconciliationError):
        calculate(lines)


def test_two_independently_balanced_lines():
    lines = evidence()
    lines.append({
        "line_id": 12,
        "company_id": 1,
        "payroll_period_id": 2,
        "reported_amount": "25.00",
        "sources": [{
            "source_id": 23,
            "source_amount": "25.00",
        }],
    })
    result = calculate(lines)
    assert result["verified_line_count"] == 2
    assert result["reported_total"] == "125.00"
    assert result["source_total"] == "125.00"
