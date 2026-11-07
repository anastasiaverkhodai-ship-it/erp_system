from copy import deepcopy
from datetime import date

import pytest

from app.services.payroll_regulatory_4df_d6_evidence_gate import (
    EvidenceGateError,
    payment_manifest,
    review_d6_basis_candidates,
    verify_manifest,
)


def allocation():
    return {
        "payroll_calculation_id": 10,
        "employee_id": 20,
        "payment_date": date(2026, 9, 30),
        "payroll_disbursement_ids": [30],
        "tax_remittance_line_ids": [40, 41],
        "payment_evidence_reference":
            "payroll_disbursements:30",
        "tax_remittance_evidence_reference":
            "payroll_tax_remittance_lines:40,41",
        "paid_amount": "770.00",
        "pit_remitted": "180.00",
        "levy_remitted": "50.00",
    }


def build(items):
    return payment_manifest(
        company_id=1,
        payroll_period_id=2,
        allocations=items,
        calculation_sources=[{
            "payroll_calculation_id": 10,
            "employee_id": 20,
        }],
    )


def test_payment_manifest_roundtrip():
    result = build([allocation()])
    assert verify_manifest(result)
    assert result["body"]["allocation_count"] == 1
    assert result["body"]["gross_paid_income_approved"] is False


def test_duplicate_posted_payment_blocked():
    with pytest.raises(EvidenceGateError, match="Reused"):
        build([allocation(), deepcopy(allocation())])


def test_wrong_employee_blocked():
    item = allocation()
    item["employee_id"] = 999
    with pytest.raises(EvidenceGateError, match="Employee"):
        build([item])


def test_invalid_payment_reference_blocked():
    item = allocation()
    item["payment_evidence_reference"] = "unverified"
    with pytest.raises(EvidenceGateError, match="Unbound"):
        build([item])


def test_source_scope_rejected():
    item = allocation()
    item["payroll_calculation_id"] = 999
    with pytest.raises(EvidenceGateError, match="Unknown"):
        build([item])


def test_payment_manifest_tampering_blocked():
    result = build([allocation()])
    result["body"]["rows"][0]["net_paid"] = "9999.00"
    with pytest.raises(EvidenceGateError, match="digest"):
        verify_manifest(result)


def test_net_payment_not_promoted_to_gross():
    result = build([allocation()])
    assert result["body"]["rows"][0]["net_paid"] == "770.00"
    assert "gross_paid" not in result["body"]["rows"][0]
    assert result["body"]["official_generation"] == "BLOCKED"


def candidate():
    return {
        "employee_id": 20,
        "contract_id": 30,
        "basis_code": "12345678",
        "period_start": date(2026, 9, 1),
        "period_end": date(2026, 9, 30),
        "basis_evidence_reference": "TEST_ONLY_DOCUMENT",
    }


def d6(items):
    return review_d6_basis_candidates(
        company_id=1,
        payroll_period_id=2,
        candidates=items,
    )


def test_d6_candidate_not_approved():
    review = d6([candidate()])
    assert review["body"]["candidate_count"] == 1
    assert review["body"]["statutory_basis_approved"] is False
    assert review["body"]["official_generation"] == "BLOCKED"


@pytest.mark.parametrize(
    "code", ["1234567", "123456789", "ABCDEFGH", None]
)
def test_invalid_d6_basis_format(code):
    item = candidate()
    item["basis_code"] = code
    with pytest.raises(EvidenceGateError, match="eight digits"):
        d6([item])


def test_duplicate_d6_evidence_rejected():
    with pytest.raises(EvidenceGateError, match="Duplicate"):
        d6([candidate(), deepcopy(candidate())])


def test_d6_reversed_period_rejected():
    item = candidate()
    item["period_start"] = date(2026, 10, 1)
    with pytest.raises(EvidenceGateError, match="before"):
        d6([item])


def test_d6_missing_document_rejected():
    item = candidate()
    item["basis_evidence_reference"] = ""
    with pytest.raises(EvidenceGateError, match="documentary"):
        d6([item])


def test_empty_d6_inventory_remains_blocked():
    review = d6([])
    assert review["body"]["candidate_count"] == 0
    assert review["body"]["official_generation"] == "BLOCKED"
