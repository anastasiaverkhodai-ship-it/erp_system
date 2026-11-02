from copy import deepcopy

import pytest

from app.services.payroll_regulatory_correction_projection import (
    build_correction_projection,
    seal_projection,
    verify_projection_seal,
)
from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)


def sample():
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

    control = {
        "company_id": 1,
        "payroll_period_id": 2,
        "report_kind": "d1",
        "financial_status": "REPORT_CORRECTION_FINANCIALS_RECONCILED",
        "financial_correction_verified": True,
        "official_form_fields_verified": False,
        "active_calculation_ids": [12],
        "excluded_historical_calculation_ids": [11],
        "failures": [],
        "official_generation": "BLOCKED",
    }
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
    return control, delta


@pytest.mark.parametrize("report", ("d1", "4df"))
def test_deterministic_projection_and_seal(report):
    control, delta = sample()
    control["report_kind"] = report

    projection = build_correction_projection(control, delta)
    seal = seal_projection(projection)

    assert verify_projection_seal(seal)
    assert seal["digest"] == seal_projection(projection)["digest"]
    assert projection["report_kind"] == report
    assert len(projection["components"]) == 3
    assert projection["active_calculation_ids"] == [12]
    assert projection["excluded_historical_calculation_ids"] == [11]
    assert seal["persistent_immutability_verified"] is False


def test_tampered_snapshot_rejected():
    control, delta = sample()
    seal = seal_projection(build_correction_projection(control, delta))
    assert verify_projection_seal(seal)

    seal["payload"]["components"][0]["tax_delta"] = "999.99"
    assert not verify_projection_seal(seal)


@pytest.mark.parametrize("mutation", (
    "missing_source",
    "different_company",
    "wrong_period",
    "wrong_tax_delta",
    "wrong_base_delta",
    "duplicate_component",
    "missing_component",
    "wrong_currency",
    "unverified_control",
    "unverified_delta",
    "cross_revision",
    "nan",
    "missing_history",
    "unsafe_generation",
))
def test_fail_closed_projection(mutation):
    control, delta = deepcopy(sample())

    if mutation == "missing_source":
        delta["components"][0]["replacement_sources"] = "1199.99"
    elif mutation == "different_company":
        delta["company_id"] = 100
    elif mutation == "wrong_period":
        delta["payroll_period_id"] = 99
    elif mutation == "wrong_tax_delta":
        delta["components"][0]["tax_delta"] = "35.99"
    elif mutation == "wrong_base_delta":
        delta["components"][0]["base_delta"] = "199.99"
    elif mutation == "duplicate_component":
        delta["components"][1]["component"] = "personal_income_tax"
    elif mutation == "missing_component":
        delta["components"].pop()
    elif mutation == "wrong_currency":
        delta["components"][0]["currency"] = "USD"
    elif mutation == "unverified_control":
        control["financial_correction_verified"] = False
    elif mutation == "unverified_delta":
        delta["persisted_correction_delta_verified"] = False
    elif mutation == "cross_revision":
        delta["active_calculation_ids"] = [99]
    elif mutation == "nan":
        delta["components"][0]["original_tax"] = "NaN"
    elif mutation == "missing_history":
        control["excluded_historical_calculation_ids"] = []
    elif mutation == "unsafe_generation":
        control["official_generation"] = "READY"

    with pytest.raises(FinancialReconciliationError):
        build_correction_projection(control, delta)
