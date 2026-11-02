from datetime import date
from copy import deepcopy

import pytest

from app.services.payroll_regulatory_active_financial_review import (
    reconcile_active_revisions,
)
from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)


def evidence():
    chain = {
        "company_id": 1,
        "payroll_period_id": 2,
        "status": "REVISION_CHAIN_VERIFIED",
        "data_origin": "PERSISTED_POSTGRESQL",
        "calculation_count": 2,
        "correction_count": 1,
        "active_calculation_ids": [12],
        "historical_calculation_ids": [11],
        "official_generation": "BLOCKED",
    }

    lines = [
        {
            "id": 101,
            "company_id": 1,
            "payroll_calculation_id": 11,
            "base_amount": "100.00",
            "amount": "18.00",
        },
        {
            "id": 102,
            "company_id": 1,
            "payroll_calculation_id": 12,
            "base_amount": "120.00",
            "amount": "21.60",
        },
    ]

    sources = [
        {
            "id": 201,
            "company_id": 1,
            "statutory_line_id": 101,
            "payroll_calculation_line_id": 301,
            "payroll_calculation_id": 11,
            "source_amount": "100.00",
            "income_effective_from": date(2026, 1, 1),
            "income_effective_to": date(2026, 1, 31),
        },
        {
            "id": 202,
            "company_id": 1,
            "statutory_line_id": 102,
            "payroll_calculation_line_id": 302,
            "payroll_calculation_id": 12,
            "source_amount": "120.00",
            "income_effective_from": date(2026, 1, 1),
            "income_effective_to": date(2026, 1, 31),
        },
    ]

    return chain, lines, sources


def run(data):
    return reconcile_active_revisions(
        *data,
        company_id=1,
        payroll_period_id=2,
    )


def test_historical_revision_excluded():
    result = run(evidence())

    assert result["status"] == "ACTIVE_FINANCIAL_RECONCILED"
    assert result["active_base_total"] == "120.00"
    assert result["active_allocated_total"] == "120.00"
    assert result["active_statutory_tax_total"] == "21.60"
    assert result["excluded_historical_base_total"] == "100.00"
    assert result["correction_delta_verified"] is False
    assert result["official_generation"] == "BLOCKED"


def test_wrong_active_source_amount():
    data = evidence()
    data[2][1]["source_amount"] = "119.99"

    result = run(data)

    assert result["status"] == "NOT_VERIFIED"


def test_missing_active_source():
    data = evidence()
    data[2].pop()

    result = run(data)

    assert result["status"] == "NOT_VERIFIED"


@pytest.mark.parametrize(
    "mutation",
    (
        "company",
        "wrong_calculation",
        "orphan",
        "duplicate",
        "dates",
        "unverified_chain",
    ),
)
def test_invalid_evidence_rejected(mutation):
    data = evidence()

    if mutation == "company":
        data[2][1]["company_id"] = 9

    elif mutation == "wrong_calculation":
        data[2][1]["payroll_calculation_id"] = 11

    elif mutation == "orphan":
        data[2][1]["statutory_line_id"] = 999

    elif mutation == "duplicate":
        duplicate = deepcopy(data[2][1])
        data[2].append(duplicate)

    elif mutation == "dates":
        data[2][1]["income_effective_to"] = date(2025, 1, 1)

    elif mutation == "unverified_chain":
        data[0]["status"] = "NOT_VERIFIED"

    with pytest.raises(FinancialReconciliationError):
        run(data)
