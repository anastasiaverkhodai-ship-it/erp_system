import copy

import pytest

from app.services.payroll_regulatory_4df_source_binding import (
    bind_4df_financial_sources,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def payroll_source(source_id=10, employee_id=3):
    return {
        "payroll_calculation_id": source_id,
        "company_id": 1,
        "payroll_period_id": 2,
        "employee_id": employee_id,
    }


def group():
    return {
        "status": "REVIEW_ONLY_NOT_FOR_FILING",
        "statutory_approved": False,
        "company_id": 1,
        "payroll_period_id": 2,
        "employee_id": 3,
        "reviewed_category_code": "REVIEWED",
        "income_month": "2026-10-01",
        "source_fingerprint": "a" * 64,
        "payroll_calculation_ids": [10],
        "payment_dates": ["2026-10-25"],
        "financial_evidence": [{
            "source_id": 10,
            "source_sha256": "b" * 64,
            "slice_number": 1,
            "slice_count": 1,
            "payment_date": "2026-10-25",
            "payment_evidence_reference": "PAY-10",
            "tax_remittance_evidence_reference": "TAX-10",
        }],
        "gross_accrued": "1000.00",
        "pit_accrued": "180.00",
        "levy_accrued": "50.00",
        "paid_amount": "770.00",
        "pit_remitted": "180.00",
        "levy_remitted": "50.00",
    }


def verify(groups=None, sources=None):
    return bind_4df_financial_sources(
        company_id=1,
        payroll_period_id=2,
        financial_groups=[group()] if groups is None else groups,
        calculation_sources=(
            [payroll_source()] if sources is None else sources
        ),
    )


def test_valid_binding():
    result = verify()

    assert result["source_count"] == 1
    assert result["slice_count"] == 1
    assert len(result["binding_sha256"]) == 64
    assert result["statutory_approved"] is False


def test_deterministic():
    assert verify() == verify()


def test_does_not_mutate_inputs():
    groups = [group()]
    sources = [payroll_source()]

    old_groups = copy.deepcopy(groups)
    old_sources = copy.deepcopy(sources)

    verify(groups, sources)

    assert groups == old_groups
    assert sources == old_sources


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id", 99),
        ("payroll_period_id", 99),
        ("employee_id", 99),
        ("status", "GENERATED"),
        ("statutory_approved", True),
        ("income_month", "2026-10-15"),
        ("source_fingerprint", "INVALID"),
        ("payment_dates", ["2026-10-26"]),
        ("financial_evidence", []),
    ],
)
def test_reject_invalid_group(field, value):
    item = group()
    item[field] = value

    with pytest.raises(PayrollRegulatoryReportError):
        verify([item])


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", 99),
        ("source_sha256", "INVALID"),
        ("slice_number", 0),
        ("slice_count", 0),
        ("slice_number", 2),
        ("payment_date", "2026-10-99"),
        ("payment_evidence_reference", ""),
        ("tax_remittance_evidence_reference", ""),
    ],
)
def test_reject_invalid_evidence(field, value):
    item = group()
    item["financial_evidence"][0][field] = value

    with pytest.raises(PayrollRegulatoryReportError):
        verify([item])


def test_reject_missing_source():
    with pytest.raises(PayrollRegulatoryReportError):
        verify(
            sources=[
                payroll_source(),
                payroll_source(11),
            ]
        )


def test_reject_duplicate_source():
    with pytest.raises(PayrollRegulatoryReportError):
        verify(
            sources=[
                payroll_source(),
                payroll_source(),
            ]
        )


def test_reject_cross_company_source():
    item = payroll_source()
    item["company_id"] = 99

    with pytest.raises(PayrollRegulatoryReportError):
        verify(sources=[item])


def test_reject_cross_period_source():
    item = payroll_source()
    item["payroll_period_id"] = 99

    with pytest.raises(PayrollRegulatoryReportError):
        verify(sources=[item])


def test_reject_wrong_employee():
    with pytest.raises(PayrollRegulatoryReportError):
        verify(sources=[payroll_source(employee_id=99)])


def test_reject_duplicate_slice():
    item = group()
    item["financial_evidence"].append(
        copy.deepcopy(item["financial_evidence"][0])
    )

    with pytest.raises(PayrollRegulatoryReportError):
        verify([item])


def test_reject_incomplete_slices():
    item = group()
    item["financial_evidence"][0]["slice_count"] = 2

    with pytest.raises(PayrollRegulatoryReportError):
        verify([item])


def test_reject_missing_payment_date():
    item = group()
    item["payment_dates"] = []

    with pytest.raises(PayrollRegulatoryReportError):
        verify([item])


def test_reject_unexpected_source():
    item = group()
    item["payroll_calculation_ids"] = [11]
    item["financial_evidence"][0]["source_id"] = 11

    with pytest.raises(PayrollRegulatoryReportError):
        verify([item])
