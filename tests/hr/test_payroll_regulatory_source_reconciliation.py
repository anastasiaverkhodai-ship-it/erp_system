from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.services.payroll_regulatory_source_reconciliation import (
    reconcile_regulatory_sources,
    RegulatorySourceReconciliationError,
)


def example():
    return {
        "company_id": 1,
        "payroll_period_id": 10,
        "report_kind": "d1",
        "calculation_sources": [{
            "employee_id": 2,
            "employment_contract_id": 3,
            "payroll_calculation_id": 4,
            "tax_number": "TEST-TAX-ID",
            "issues": [],
            "gross_amount": D("1000.00"),
            "net_amount": D("770.00"),
            "employee_withholding_amount": D("230.00"),
            "employer_contribution_amount": D("220.00"),
            "currency_code": "UAH",
            "components": [
                {
                    "source_line_id": 11,
                    "component": "personal_income_tax",
                    "base_amount": D("1000.00"),
                    "rate": D("0.18"),
                    "amount": D("180.00"),
                    "currency_code": "UAH",
                },
                {
                    "source_line_id": 12,
                    "component": "military_levy",
                    "base_amount": D("1000.00"),
                    "rate": D("0.05"),
                    "amount": D("50.00"),
                    "currency_code": "UAH",
                },
                {
                    "source_line_id": 13,
                    "component": "unified_social_contribution",
                    "base_amount": D("1000.00"),
                    "rate": D("0.22"),
                    "amount": D("220.00"),
                    "currency_code": "UAH",
                },
            ],
        }],
    }


def test_complete_frozen_source_reconciles():
    totals = reconcile_regulatory_sources(example())
    assert totals.source_count == 1
    assert totals.gross == D("1000.00")
    assert totals.net == D("770.00")
    assert totals.pit == D("180.00")
    assert totals.military_levy == D("50.00")
    assert totals.usc == D("220.00")


def test_missing_statutory_result_rejected():
    data = example()
    data["calculation_sources"][0]["issues"] = [
        "STATUTORY_CALCULATION_MISSING"
    ]
    with pytest.raises(RegulatorySourceReconciliationError):
        reconcile_regulatory_sources(data)


def test_gross_net_tampering_rejected():
    data = example()
    data["calculation_sources"][0]["net_amount"] = D("900")
    with pytest.raises(
        RegulatorySourceReconciliationError,
        match="do not reconcile",
    ):
        reconcile_regulatory_sources(data)


def test_tax_component_tampering_rejected():
    data = example()
    data["calculation_sources"][0]["components"][0][
        "amount"
    ] = D("170.00")
    with pytest.raises(
        RegulatorySourceReconciliationError,
        match="PIT and levy",
    ):
        reconcile_regulatory_sources(data)


def test_usc_component_tampering_rejected():
    data = example()
    data["calculation_sources"][0]["components"][2][
        "amount"
    ] = D("250.00")
    with pytest.raises(
        RegulatorySourceReconciliationError,
        match="USC",
    ):
        reconcile_regulatory_sources(data)


def test_duplicate_current_calculation_rejected():
    data = example()
    data["calculation_sources"].append(
        deepcopy(data["calculation_sources"][0])
    )
    with pytest.raises(
        RegulatorySourceReconciliationError,
        match="Duplicate current payroll",
    ):
        reconcile_regulatory_sources(data)


def test_wrong_currency_rejected():
    data = example()
    data["calculation_sources"][0]["currency_code"] = "EUR"
    with pytest.raises(
        RegulatorySourceReconciliationError,
        match="currency",
    ):
        reconcile_regulatory_sources(data)
