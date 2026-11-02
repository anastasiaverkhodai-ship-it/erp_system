from copy import deepcopy

import pytest

from app.services.payroll_regulatory_correction_report_control import (
    evaluate_correction_report_control,
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
        "active_calculation_ids": [12],
        "historical_calculation_ids": [11],
        "official_generation": "BLOCKED",
    }

    active = {
        "company_id": 1,
        "payroll_period_id": 2,
        "status": "ACTIVE_FINANCIAL_RECONCILED",
        "data_origin": "PERSISTED_POSTGRESQL",
        "active_calculation_count": 1,
        "historical_calculation_count": 1,
        "active_base_total": "3600.00",
        "active_allocated_total": "3600.00",
        "errors": [],
        "official_generation": "BLOCKED",
    }

    components = []
    for component in (
        "personal_income_tax",
        "military_levy",
        "unified_social_contribution",
    ):
        components.append({
            "component": component,
            "currency": "UAH",
            "original_base": "1000.00",
            "replacement_base": "1200.00",
            "base_delta": "200.00",
            "original_tax": "180.00",
            "replacement_tax": "216.00",
            "tax_delta": "36.00",
            "original_sources": "1000.00",
            "replacement_sources": "1200.00",
        })

    delta = {
        "company_id": 1,
        "payroll_period_id": 2,
        "status": "CORRECTION_COMPONENT_DELTA_RECONCILED",
        "persisted_correction_delta_verified": True,
        "correction_chain_provenance": "PERSISTED_POSTGRESQL",
        "active_calculation_ids": [12],
        "historical_calculation_ids": [11],
        "components": components,
        "missing_source_line_ids": [],
        "mismatched_source_line_ids": [],
        "official_generation": "BLOCKED",
    }

    return chain, active, delta


def evaluate(data, report="d1"):
    return evaluate_correction_report_control(
        chain=data[0],
        active=data[1],
        delta=data[2],
        company_id=1,
        payroll_period_id=2,
        report_kind=report,
    )


@pytest.mark.parametrize("report", ("d1", "4df"))
def test_positive_financial_control(report):
    result = evaluate(evidence(), report)
    assert result["financial_status"] == (
        "REPORT_CORRECTION_FINANCIALS_RECONCILED"
    )
    assert result["financial_correction_verified"] is True
    assert result["excluded_historical_calculation_ids"] == [11]
    assert result["official_form_fields_verified"] is False
    assert result["immutable_report_snapshot_verified"] is False
    assert result["official_generation"] == "BLOCKED"


@pytest.mark.parametrize("mutation", (
    "missing_history",
    "unverified_delta",
    "wrong_company",
    "wrong_period",
    "wrong_source",
    "duplicate_component",
    "missing_component",
    "wrong_amount",
    "unverified_active",
    "unsafe_generation",
))
def test_negative_control(mutation):
    data = list(deepcopy(evidence()))

    if mutation == "missing_history":
        data[0]["historical_calculation_ids"] = []
    elif mutation == "unverified_delta":
        data[2]["persisted_correction_delta_verified"] = False
    elif mutation == "wrong_company":
        data[2]["company_id"] = 10
    elif mutation == "wrong_period":
        data[1]["payroll_period_id"] = 10
    elif mutation == "wrong_source":
        data[2]["components"][0]["replacement_sources"] = "1199.99"
    elif mutation == "duplicate_component":
        data[2]["components"].append(
            deepcopy(data[2]["components"][0])
        )
    elif mutation == "missing_component":
        data[2]["components"].pop()
    elif mutation == "wrong_amount":
        data[2]["components"][0]["tax_delta"] = "35.99"
    elif mutation == "unverified_active":
        data[1]["status"] = "NOT_VERIFIED"
    elif mutation == "unsafe_generation":
        data[0]["official_generation"] = "READY"

    result = evaluate(data)
    assert result["financial_correction_verified"] is False
    assert result["financial_status"] == "NOT_VERIFIED"
    assert result["official_generation"] == "BLOCKED"


def test_invalid_report_rejected():
    with pytest.raises(FinancialReconciliationError):
        evaluate(evidence(), "d5")
