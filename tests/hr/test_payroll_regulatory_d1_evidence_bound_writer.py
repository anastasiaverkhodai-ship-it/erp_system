"""Fail-closed immutable aggregate evidence unit tests."""

from copy import deepcopy

import pytest

from app.services.payroll_regulatory_d1_evidence_bound_writer import (
    D1EvidenceBindingError,
    make_anchor,
    verify_anchor,
    serialize_anchor,
    deserialize_anchor,
)


def control():
    return {
        "company_id": 1,
        "payroll_period_id": 2,
        "source_count": 1,
        "gross_total": "100.00",
        "employee_withholding_total": "20.00",
        "employer_contribution_total": "22.00",
        "d1_source_review_checked": True,
        "gross_independently_reconciled": True,
        "separate_pit_verified": False,
        "separate_military_levy_verified": False,
        "payment_evidence_verified": False,
        "official_generation": "BLOCKED",
    }


def test_anchor_roundtrip():
    anchor = make_anchor(control())

    assert len(anchor["sha256"]) == 64
    assert verify_anchor(anchor) is True
    assert make_anchor(control()) == anchor


@pytest.mark.parametrize(
    "field,value",
    [
        ("gross_total", "101.00"),
        ("employee_withholding_total", "21.00"),
        ("employer_contribution_total", "23.00"),
        ("source_count", 2),
    ],
)
def test_financial_tampering_detected(field, value):
    original = make_anchor(control())
    modified = deepcopy(original)
    modified["control"][field] = value

    with pytest.raises(D1EvidenceBindingError):
        verify_anchor(modified)


def test_status_escalation_rejected():
    changed = control()
    changed["official_generation"] = "ALLOWED"

    with pytest.raises(D1EvidenceBindingError):
        make_anchor(changed)


def test_separate_tax_approval_rejected():
    changed = control()
    changed["separate_pit_verified"] = True

    with pytest.raises(D1EvidenceBindingError):
        make_anchor(changed)


def test_missing_hash_rejected():
    changed = make_anchor(control())
    del changed["sha256"]

    with pytest.raises(D1EvidenceBindingError):
        verify_anchor(changed)



def test_serialized_evidence_roundtrip():
    original = make_anchor(control())
    serialized = serialize_anchor(original)

    assert isinstance(serialized, str)
    assert serialized
    assert deserialize_anchor(serialized) == original


def test_noncanonical_evidence_rejected():
    original = make_anchor(control())

    import json

    noncanonical = json.dumps(
        original,
        indent=2,
        ensure_ascii=False,
    )

    with pytest.raises(D1EvidenceBindingError):
        deserialize_anchor(noncanonical)


def test_tampered_serialized_amount_rejected():
    import json

    original = make_anchor(control())
    changed = json.loads(serialize_anchor(original))
    changed["control"]["gross_total"] = "999.00"

    with pytest.raises(D1EvidenceBindingError):
        deserialize_anchor(
            json.dumps(
                changed,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


def test_object_not_accepted_as_persisted_attribute():
    original = make_anchor(control())

    with pytest.raises(D1EvidenceBindingError):
        deserialize_anchor(original)
