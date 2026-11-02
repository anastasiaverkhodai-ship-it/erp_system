from copy import deepcopy

import pytest

from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)

from app.services.payroll_regulatory_persisted_allocation import (
    verify_allocations,
    one_foreign_key,
)


def sample():
    lines = [{
        "id": 11,
        "company_id": 1,
        "payroll_period_id": 2,
        "reported_amount": "120.00",
    }]

    sources = [
        {
            "id": 21,
            "statutory_line_id": 11,
            "payroll_calculation_line_id": 31,
            "company_id": 1,
            "source_amount": "70.00",
        },
        {
            "id": 22,
            "statutory_line_id": 11,
            "payroll_calculation_line_id": 32,
            "company_id": 1,
            "source_amount": "50.00",
        },
    ]

    calculations = [
        {"id": 31, "company_id": 1, "payroll_period_id": 2},
        {"id": 32, "company_id": 1, "payroll_period_id": 2},
    ]

    return lines, sources, calculations


def check(lines, sources, calculations):
    return verify_allocations(
        lines, sources, calculations,
        company_id=1,
        payroll_period_id=2,
    )


def test_persisted_allocation_math():
    result = check(*sample())
    assert result["status"] == "FINANCIAL_LINES_RECONCILED"
    assert result["verified_lines"] == 1
    assert result["reported_total"] == "120.00"
    assert result["allocated_total"] == "120.00"
    assert result["official_generation"] == "BLOCKED"


def test_incorrect_persisted_amount_detected():
    lines, sources, calculations = sample()
    sources[1]["source_amount"] = "49.99"
    result = check(lines, sources, calculations)
    assert result["status"] == "NOT_VERIFIED"
    assert result["lines"][0]["difference"] == "0.01"


def test_missing_source_blocks_verification():
    lines, sources, calculations = sample()
    result = check(lines, [], calculations)
    assert result["status"] == "NOT_VERIFIED"


@pytest.mark.parametrize(
    "change",
    [
        "company",
        "period",
        "duplicate_line",
        "duplicate_source",
        "missing_calculation",
        "orphan_source",
    ],
)
def test_invalid_persisted_source_rejected(change):
    lines, sources, calculations = sample()

    if change == "company":
        sources[0]["company_id"] = 999
    elif change == "period":
        calculations[0]["payroll_period_id"] = 999
    elif change == "duplicate_line":
        lines.append(deepcopy(lines[0]))
    elif change == "duplicate_source":
        sources.append(deepcopy(sources[0]))
    elif change == "missing_calculation":
        calculations.pop(0)
    elif change == "orphan_source":
        sources[0]["statutory_line_id"] = 999

    with pytest.raises(FinancialReconciliationError):
        check(lines, sources, calculations)


def test_empty_persisted_lines_not_verified():
    result = check([], [], [])
    assert result["status"] == "NOT_VERIFIED"


def test_ambiguous_foreign_key_rejected():
    schema = {
        "child": {
            "foreign_keys": [
                {
                    "referred_table": "parent",
                    "constrained_columns": ["first"],
                    "referred_columns": ["id"],
                },
                {
                    "referred_table": "parent",
                    "constrained_columns": ["second"],
                    "referred_columns": ["id"],
                },
            ],
        }
    }

    assert one_foreign_key(schema, "child", "parent") is None
