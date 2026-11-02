from copy import deepcopy

import pytest

from app.services.payroll_regulatory_revision_chain_review import (
    review_revision_chain,
)
from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)


def sample():
    calculations = [
        {
            "id": 10,
            "company_id": 1,
            "payroll_period_id": 2,
            "employment_contract_id": 5,
            "revision": 1,
        },
        {
            "id": 11,
            "company_id": 1,
            "payroll_period_id": 2,
            "employment_contract_id": 5,
            "revision": 2,
        },
    ]
    corrections = [{
        "company_id": 1,
        "original_payroll_calculation_id": 10,
        "replacement_payroll_calculation_id": 11,
    }]
    statutory = [
        {
            "company_id": 1,
            "payroll_calculation_id": 10,
        },
        {
            "company_id": 1,
            "payroll_calculation_id": 11,
        },
    ]
    return calculations, corrections, statutory


def evaluate(parts):
    return review_revision_chain(
        *parts,
        company_id=1,
        payroll_period_id=2,
    )


def test_valid_replacement_chain():
    result = evaluate(sample())
    assert result["status"] == "REVISION_CHAIN_VERIFIED"
    assert result["active_calculation_ids"] == [11]
    assert result["historical_calculation_ids"] == [10]
    assert result["correction_financial_reconciliation"] == "NOT_VERIFIED"
    assert result["official_generation"] == "BLOCKED"


def test_no_correction_single_revision():
    calculations, links, statutory = sample()
    calculations.pop()
    links.clear()
    statutory.pop()
    result = evaluate((calculations, links, statutory))
    assert result["status"] == "REVISION_CHAIN_VERIFIED"
    assert result["active_calculation_ids"] == [10]


def test_missing_active_statutory_blocks():
    parts = sample()
    parts[2].pop()
    result = evaluate(parts)
    assert result["status"] == "NOT_VERIFIED"
    assert result["missing_active_statutory_ids"] == [11]


def test_duplicate_active_statutory_blocks():
    parts = sample()
    parts[2].append(deepcopy(parts[2][-1]))
    result = evaluate(parts)
    assert result["status"] == "NOT_VERIFIED"


@pytest.mark.parametrize(
    "mutation",
    [
        "cross_company",
        "cross_period",
        "missing_link",
        "self_link",
        "reversed_revision",
        "fork",
        "duplicate_calculation",
        "unknown_replacement",
    ],
)
def test_invalid_revision_chain_rejected(mutation):
    parts = sample()

    if mutation == "cross_company":
        parts[0][0]["company_id"] = 999
    elif mutation == "cross_period":
        parts[0][1]["payroll_period_id"] = 999
    elif mutation == "missing_link":
        parts[1].clear()
    elif mutation == "self_link":
        parts[1][0]["replacement_payroll_calculation_id"] = 10
    elif mutation == "reversed_revision":
        parts[0][1]["revision"] = 1
    elif mutation == "fork":
        third = deepcopy(parts[0][1])
        third["id"] = 12
        third["revision"] = 3
        parts[0].append(third)
        parts[1].append({
            "company_id": 1,
            "original_payroll_calculation_id": 10,
            "replacement_payroll_calculation_id": 12,
        })
    elif mutation == "duplicate_calculation":
        parts[0].append(deepcopy(parts[0][0]))
    elif mutation == "unknown_replacement":
        parts[1][0]["replacement_payroll_calculation_id"] = 999

    with pytest.raises(FinancialReconciliationError):
        evaluate(parts)


def test_multiple_employees_without_correction():
    calculations, links, statutory = sample()
    calculations.pop()
    links.clear()
    statutory.pop()

    second = deepcopy(calculations[0])
    second["id"] = 20
    second["employment_contract_id"] = 6
    calculations.append(second)
    statutory.append({
        "company_id": 1,
        "payroll_calculation_id": 20,
    })

    result = evaluate((calculations, links, statutory))
    assert result["status"] == "REVISION_CHAIN_VERIFIED"
    assert result["active_calculation_ids"] == [10, 20]
