from copy import deepcopy

import pytest

from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)
from app.services.payroll_regulatory_period_coverage import (
    evaluate_period_coverage,
)


def evidence():
    calculations = [{
        "id": 10,
        "company_id": 1,
        "payroll_period_id": 2,
        "employment_contract_id": 5,
        "revision": 1,
    }]
    results = [{
        "company_id": 1,
        "payroll_calculation_id": 10,
    }]
    allocation = {
        "company_id": 1,
        "payroll_period_id": 2,
        "status": "FINANCIAL_LINES_RECONCILED",
        "data_origin": "PERSISTED_POSTGRESQL",
        "line_count": 4,
        "verified_lines": 4,
        "reported_total": "1000.00",
        "allocated_total": "1000.00",
        "statutory_mapping_verified": False,
        "official_generation": "BLOCKED",
    }
    return calculations, results, allocation


def check(parts):
    return evaluate_period_coverage(
        *parts,
        company_id=1,
        payroll_period_id=2,
    )


def test_complete_period():
    result = check(evidence())
    assert result["status"] == "PERIOD_FINANCIAL_COVERAGE_VERIFIED"
    assert result["official_generation"] == "BLOCKED"


def test_missing_statutory_result():
    parts = evidence()
    parts[1].clear()
    result = check(parts)
    assert result["status"] == "NOT_VERIFIED"
    assert result["missing_calculation_ids"] == [10]


def test_missing_all_calculations():
    parts = evidence()
    parts[0].clear()
    parts[1].clear()
    assert check(parts)["status"] == "NOT_VERIFIED"


def test_ambiguous_revisions_blocked():
    parts = evidence()
    replacement = deepcopy(parts[0][0])
    replacement["id"] = 11
    replacement["revision"] = 2
    parts[0].append(replacement)
    parts[1].append({
        "company_id": 1,
        "payroll_calculation_id": 11,
    })
    result = check(parts)
    assert result["status"] == "NOT_VERIFIED"
    assert result["ambiguous_revision_contract_ids"] == [5]


def test_duplicate_statutory_result_blocked():
    parts = evidence()
    parts[1].append(deepcopy(parts[1][0]))
    result = check(parts)
    assert result["status"] == "NOT_VERIFIED"


@pytest.mark.parametrize(
    "key,value",
    [
        ("status", "NOT_VERIFIED"),
        ("data_origin", "SYNTHETIC"),
        ("verified_lines", 3),
        ("reported_total", "999.99"),
        ("official_generation", "READY"),
        ("statutory_mapping_verified", True),
    ],
)
def test_invalid_allocation_blocks(key, value):
    parts = evidence()
    parts[2][key] = value
    assert check(parts)["status"] == "NOT_VERIFIED"


def test_cross_company_rejected():
    parts = evidence()
    parts[0][0]["company_id"] = 999
    with pytest.raises(FinancialReconciliationError):
        check(parts)


def test_orphan_result_rejected():
    parts = evidence()
    parts[1][0]["payroll_calculation_id"] = 999
    with pytest.raises(FinancialReconciliationError):
        check(parts)
