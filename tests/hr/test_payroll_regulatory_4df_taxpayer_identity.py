from copy import deepcopy
from datetime import date

import pytest

from app.services.payroll_regulatory_4df_taxpayer_identity import (
    attach_reviewed_4df_taxpayer_identities,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def scenario():
    groups = [
        {
            "company_id": 7,
            "employee_id": 11,
            "payroll_calculation_ids": [101, 102],
            "status": "REVIEW_ONLY_NOT_FOR_FILING",
            "statutory_approved": False,
            "gross_accrued": "2000.00",
            "paid_amount": "1500.00",
        }
    ]

    sources = [
        {
            "payroll_calculation_id": 101,
            "employee_id": 11,
            "tax_number": "1234567890",
        },
        {
            "payroll_calculation_id": 102,
            "employee_id": 11,
            "tax_number": "1234567890",
        },
    ]

    reviewed = [
        {
            "company_id": 7,
            "employee_id": 11,
            "identity_kind": "rnokpp",
            "tax_number": "1234567890",
            "evidence_reference": "REVIEW-DOCUMENT-101",
            "reviewed_by": 5,
            "reviewed_on": date(2026, 10, 20),
        }
    ]

    return groups, sources, reviewed


def run(groups, sources, reviewed):
    return attach_reviewed_4df_taxpayer_identities(
        company_id=7,
        financial_groups=groups,
        calculation_sources=sources,
        reviewed_identities=reviewed,
    )


def test_identity_linked_to_every_source():
    groups, sources, reviewed = scenario()
    result = run(groups, sources, reviewed)

    assert len(result) == 1
    assert result[0]["taxpayer_identity"]["tax_number"] == (
        "1234567890"
    )
    assert result[0]["status"] == (
        "IDENTITY_REVIEWED_NOT_FOR_FILING"
    )
    assert result[0]["statutory_approved"] is False
    assert result[0]["gross_accrued"] == "2000.00"


def test_original_financial_groups_unchanged():
    groups, sources, reviewed = scenario()
    original = deepcopy(groups)
    run(groups, sources, reviewed)
    assert groups == original


def test_missing_review_rejected():
    groups, sources, reviewed = scenario()

    with pytest.raises(PayrollRegulatoryReportError):
        run(groups, sources, [])


def test_tax_number_mismatch_rejected():
    groups, sources, reviewed = scenario()
    reviewed[0]["tax_number"] = "0987654321"

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="disagrees",
    ):
        run(groups, sources, reviewed)


def test_missing_payroll_calculation_rejected():
    groups, sources, reviewed = scenario()
    groups[0]["payroll_calculation_ids"] = [101]

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="exactly",
    ):
        run(groups, sources, reviewed)


def test_cross_company_review_rejected():
    groups, sources, reviewed = scenario()
    reviewed[0]["company_id"] = 8

    with pytest.raises(PayrollRegulatoryReportError):
        run(groups, sources, reviewed)


def test_duplicate_identity_review_rejected():
    groups, sources, reviewed = scenario()
    reviewed.append(deepcopy(reviewed[0]))

    with pytest.raises(PayrollRegulatoryReportError):
        run(groups, sources, reviewed)


def test_passport_not_automatically_treated_as_rnokpp():
    groups, sources, reviewed = scenario()
    reviewed[0]["identity_kind"] = "passport"

    with pytest.raises(PayrollRegulatoryReportError):
        run(groups, sources, reviewed)


def test_invalid_tax_identifier_rejected():
    groups, sources, reviewed = scenario()
    reviewed[0]["tax_number"] = "ABC123"

    with pytest.raises(PayrollRegulatoryReportError):
        run(groups, sources, reviewed)


def test_repeated_calculation_rejected():
    groups, sources, reviewed = scenario()
    groups.append(deepcopy(groups[0]))

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="multiple times",
    ):
        run(groups, sources, reviewed)
