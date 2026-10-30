import copy

import pytest

from app.services.payroll_regulatory_4df_integrity import (
    verify_4df_financial_projection,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def valid_group():
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
        "gross_accrued": "1000.00",
        "pit_accrued": "180.00",
        "levy_accrued": "50.00",
        "paid_amount": "770.00",
        "pit_remitted": "180.00",
        "levy_remitted": "50.00",
    }


def verify(groups, expected=(10,)):
    return verify_4df_financial_projection(
        company_id=1,
        payroll_period_id=2,
        financial_groups=groups,
        expected_calculation_ids=list(expected),
    )


def test_valid_manifest_is_deterministic():
    group = valid_group()

    first = verify([group])
    second = verify([copy.deepcopy(group)])

    assert first == second
    assert first["source_count"] == 1
    assert first["monthly_totals"][0]["totals"][
        "gross_accrued"
    ] == "1000.00"
    assert first["statutory_approved"] is False
    assert first["status"] == "REVIEW_ONLY_NOT_FOR_FILING"


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id", 99),
        ("payroll_period_id", 99),
        ("status", "GENERATED"),
        ("statutory_approved", True),
        ("employee_id", 0),
        ("reviewed_category_code", ""),
        ("income_month", "2026-10-15"),
        ("income_month", "2026-13-01"),
        ("source_fingerprint", "invalid"),
        ("gross_accrued", "-1.00"),
        ("gross_accrued", "NaN"),
        ("pit_accrued", "180.001"),
        ("pit_remitted", "181.00"),
        ("levy_remitted", "51.00"),
    ],
)
def test_reject_invalid_groups(field, value):
    group = valid_group()
    group[field] = value

    with pytest.raises(PayrollRegulatoryReportError):
        verify([group])


def test_reject_missing_source():
    with pytest.raises(PayrollRegulatoryReportError):
        verify([valid_group()], expected=(10, 11))


def test_reject_duplicate_source():
    group = valid_group()

    with pytest.raises(PayrollRegulatoryReportError):
        verify([group, copy.deepcopy(group)])


def test_reject_duplicate_source_inside_group():
    group = valid_group()
    group["payroll_calculation_ids"] = [10, 10]

    with pytest.raises(PayrollRegulatoryReportError):
        verify([group])


def test_reject_unexpected_source():
    with pytest.raises(PayrollRegulatoryReportError):
        verify([valid_group()], expected=(11,))


def test_reject_empty_projection():
    with pytest.raises(PayrollRegulatoryReportError):
        verify([])


def test_reject_floating_point_financial_values():
    group = valid_group()
    group["gross_accrued"] = 1000.0

    with pytest.raises(PayrollRegulatoryReportError):
        verify([group])


def test_reject_duplicate_expected_sources():
    with pytest.raises(PayrollRegulatoryReportError):
        verify([valid_group()], expected=(10, 10))
