from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.payroll_regulatory_4df_split_allocation import (
    FourDFSplitAllocationError,
    build_split_4df_draft_rows,
)


def scenario():
    report = SimpleNamespace(
        company_id=1,
        payroll_period_id=2,
        report_kind="4df",
    )
    readiness = {
        "company_id": 1,
        "payroll_period_id": 2,
        "report_kind": "4df",
        "blocking_issues": ["DATED_STATUTORY_FORM_MAPPING_MISSING"],
        "calculation_sources": [{
            "payroll_calculation_id": 10,
            "employee_id": 20,
            "employment_contract_id": 30,
            "issues": [],
            "gross_amount": "1000.00",
            "components": [
                {
                    "source_line_id": 1,
                    "component": "personal_income_tax",
                    "amount": "180.00",
                },
                {
                    "source_line_id": 2,
                    "component": "military_levy",
                    "amount": "50.00",
                },
            ],
        }],
    }
    mapping = SimpleNamespace(
        official_reference="review-source-ref",
        source_sha256="a" * 64,
    )
    allocations = []
    for number, gross, pit, levy in (
        (1, "600.00", "108.00", "30.00"),
        (2, "400.00", "72.00", "20.00"),
    ):
        allocations.append({
            "payroll_calculation_id": 10,
            "slice_number": number,
            "slice_count": 2,
            "normative_mapping_id": 5,
            "official_reference": "review-source-ref",
            "normative_source_sha256": "a" * 64,
            "review_reference": f"review-{number}",
            "category_code": "review-category",
            "income_month": date(2026, 10, 1),
            "payment_date": date(2026, 10, 30),
            "accrued_amount": gross,
            "pit_accrued": pit,
            "levy_accrued": levy,
            "paid_amount": gross,
            "pit_remitted": pit,
            "levy_remitted": levy,
            "payment_evidence_reference": f"payment-{number}",
            "tax_remittance_evidence_reference": f"tax-{number}",
        })
    return report, readiness, allocations, {5: mapping}


def build(values):
    report, readiness, allocations, approvals = values
    return build_split_4df_draft_rows(
        report=report,
        readiness=readiness,
        evidence=allocations,
        approvals=approvals,
    )


def test_positive_two_slice_reconciliation():
    rows = build(scenario())
    assert len(rows) == 2
    assert all(r["row_code"] == "4DF_SPLIT_REVIEWED_SOURCE_V1"
               for r in rows)
    assert all('"official_generation":"BLOCKED"' in r["payload_json"]
               for r in rows)


@pytest.mark.parametrize("field,value", [
    ("slice_number", 1),
    ("slice_count", 3),
    ("accrued_amount", "399.00"),
    ("pit_accrued", "71.00"),
    ("levy_accrued", "19.00"),
    ("paid_amount", "500.00"),
    ("normative_mapping_id", 999),
    ("payment_evidence_reference", ""),
    ("income_month", date(2026, 10, 2)),
])
def test_invalid_split_rejected(field, value):
    values = list(scenario())
    values[2] = deepcopy(values[2])
    values[2][1][field] = value
    with pytest.raises(FourDFSplitAllocationError):
        build(values)


def test_incomplete_source_coverage_rejected():
    values = list(scenario())
    values[2] = values[2][:1]
    with pytest.raises(FourDFSplitAllocationError):
        build(values)


def test_cross_company_rejected():
    values = list(scenario())
    values[1] = dict(values[1], company_id=999)
    with pytest.raises(FourDFSplitAllocationError):
        build(values)


def test_unresolved_source_rejected():
    values = list(scenario())
    values[1] = deepcopy(values[1])
    values[1]["calculation_sources"][0]["issues"] = ["UNVERIFIED"]
    with pytest.raises(FourDFSplitAllocationError):
        build(values)
