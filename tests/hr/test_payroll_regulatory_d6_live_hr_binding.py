from copy import deepcopy

import pytest

from app.services.payroll_regulatory_4df_d6_evidence_gate import (
    EvidenceGateError,
    fingerprint,
)
from app.services.payroll_regulatory_d6_live_hr_binding import (
    verify_binding,
)


def sample():
    body = {
        "schema": "ERP_D6_LIVE_HR_SOURCE_BINDING_V1",
        "company_id": 1,
        "payroll_period_id": 2,
        "candidate_count": 1,
        "candidates": [{
            "employee_id": 3,
            "contract_id": 4,
            "hr_binding": "VERIFIED",
            "basis_code_mapping": "NOT_APPROVED",
            "special_service_basis": "NOT_VERIFIED",
        }],
        "hr_source_binding": "LIVE_POSTGRESQL_VERIFIED",
        "normative_code_mapping": "NOT_APPROVED",
        "special_service_eligibility": "NOT_VERIFIED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
    return {"body": body, "sha256": fingerprint(body)}


def test_binding_roundtrip():
    assert verify_binding(sample()) is True


@pytest.mark.parametrize("field,value", [
    ("normative_code_mapping", "APPROVED"),
    ("special_service_eligibility", "VERIFIED"),
    ("official_generation", "ENABLED"),
    ("official_rows_generated", 1),
    ("hr_source_binding", "NOT_VERIFIED"),
])
def test_unapproved_claim_blocked(field, value):
    doc = sample()
    doc["body"][field] = value
    doc["sha256"] = fingerprint(doc["body"])
    with pytest.raises(EvidenceGateError):
        verify_binding(doc)


def test_candidate_tamper_blocked():
    doc = sample()
    doc["body"]["candidates"][0]["contract_id"] = 99
    with pytest.raises(EvidenceGateError, match="digest"):
        verify_binding(doc)


def test_candidate_count_mismatch_blocked():
    doc = sample()
    doc["body"]["candidate_count"] = 0
    doc["sha256"] = fingerprint(doc["body"])
    with pytest.raises(EvidenceGateError, match="count"):
        verify_binding(doc)


def test_candidate_approval_blocked():
    doc = sample()
    doc["body"]["candidates"][0]["basis_code_mapping"] = "APPROVED"
    doc["sha256"] = fingerprint(doc["body"])
    with pytest.raises(EvidenceGateError):
        verify_binding(doc)


def test_input_not_mutated():
    doc = sample()
    before = deepcopy(doc)
    assert verify_binding(doc)
    assert doc == before
