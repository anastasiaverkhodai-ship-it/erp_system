from copy import deepcopy
from datetime import date

import pytest

from app.services.payroll_regulatory_correction_delta import (
    reconcile_correction_delta,
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
        "active_calculation_ids": [12],
        "historical_calculation_ids": [11],
        "official_generation": "BLOCKED",
    }

    lines = [
        {
            "id": 101,
            "company_id": 1,
            "payroll_calculation_id": 11,
            "component": "personal_income_tax",
            "currency_code": "UAH",
            "base_amount": "1000.00",
            "amount": "180.00",
        },
        {
            "id": 102,
            "company_id": 1,
            "payroll_calculation_id": 12,
            "component": "personal_income_tax",
            "currency_code": "UAH",
            "base_amount": "1200.00",
            "amount": "216.00",
        },
    ]

    sources = [
        {
            "id": 201,
            "company_id": 1,
            "statutory_line_id": 101,
            "payroll_calculation_line_id": 301,
            "payroll_calculation_id": 11,
            "source_amount": "1000.00",
            "income_effective_from": date(2026, 1, 1),
            "income_effective_to": date(2026, 1, 31),
        },
        {
            "id": 202,
            "company_id": 1,
            "statutory_line_id": 102,
            "payroll_calculation_line_id": 302,
            "payroll_calculation_id": 12,
            "source_amount": "1200.00",
            "income_effective_from": date(2026, 1, 1),
            "income_effective_to": date(2026, 1, 31),
        },
    ]

    return chain, lines, sources


def run(data):
    return reconcile_correction_delta(
        *data,
        company_id=1,
        payroll_period_id=2,
    )


def test_positive_correction_delta():
    result = run(evidence())
    assert result["status"] == (
        "CORRECTION_COMPONENT_DELTA_RECONCILED"
    )
    row = result["components"][0]
    assert row["original_base"] == "1000.00"
    assert row["replacement_base"] == "1200.00"
    assert row["base_delta"] == "200.00"
    assert row["original_tax"] == "180.00"
    assert row["replacement_tax"] == "216.00"
    assert row["tax_delta"] == "36.00"
    assert result["historical_double_count_in_active_totals"] is False
    assert result["persisted_correction_delta_verified"] is False
    assert result["official_generation"] == "BLOCKED"


def test_negative_correction_delta():
    data = evidence()
    data[1][1]["base_amount"] = "800.00"
    data[1][1]["amount"] = "144.00"
    data[2][1]["source_amount"] = "800.00"
    result = run(data)
    assert result["status"] == (
        "CORRECTION_COMPONENT_DELTA_RECONCILED"
    )
    assert result["components"][0]["base_delta"] == "-200.00"
    assert result["components"][0]["tax_delta"] == "-36.00"


def test_missing_replacement_source_blocks():
    data = evidence()
    data[2].pop()
    result = run(data)
    assert result["status"] == "NOT_VERIFIED"
    assert result["missing_source_line_ids"] == [102]


def test_wrong_source_base_blocks():
    data = evidence()
    data[2][1]["source_amount"] = "1199.99"
    assert run(data)["status"] == "NOT_VERIFIED"


@pytest.mark.parametrize(
    "mutation",
    (
        "wrong_company",
        "wrong_calculation",
        "orphan",
        "duplicate_source",
        "invalid_date",
        "invalid_chain",
        "missing_history",
        "nan",
    ),
)
def test_invalid_correction_evidence(mutation):
    data = evidence()

    if mutation == "wrong_company":
        data[2][1]["company_id"] = 999
    elif mutation == "wrong_calculation":
        data[2][1]["payroll_calculation_id"] = 11
    elif mutation == "orphan":
        data[2][1]["statutory_line_id"] = 999
    elif mutation == "duplicate_source":
        data[2].append(deepcopy(data[2][1]))
    elif mutation == "invalid_date":
        data[2][1]["income_effective_to"] = date(2025, 1, 1)
    elif mutation == "invalid_chain":
        data[0]["status"] = "NOT_VERIFIED"
    elif mutation == "missing_history":
        data[0]["historical_calculation_ids"] = []
    elif mutation == "nan":
        data[1][1]["amount"] = "NaN"

    with pytest.raises(FinancialReconciliationError):
        run(data)
