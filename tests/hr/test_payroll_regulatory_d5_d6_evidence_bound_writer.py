"""D5/D6 canonical HR inventory evidence unit tests."""

import json

import pytest

from app.services.payroll_regulatory_d5_d6_evidence_bound_writer import (
    HRInventoryBindingError,
    make_anchor,
    verify_anchor,
)


def review():
    return {
        "company_id": 10,
        "payroll_period_id": 20,
        "review_only": True,
        "overall_status": "REVIEW_ONLY",
        "official_statutory_generation": "BLOCKED",
        "snapshot_sha256": "a" * 64,
        "source_record_count": 3,
        "d5": {
            "evidence_collection": "VERIFIED",
            "statutory_event_mapping": "NOT_APPROVED",
            "official_rows_generated": 0,
        },
        "d6": {
            "applicability": "NOT_DETERMINED",
            "normative_mapping": "NOT_APPROVED",
            "official_rows_generated": 0,
        },
    }


def test_anchor_roundtrip():
    value = make_anchor(review())
    checked = verify_anchor(value)

    assert checked["source_sha256"] == "a" * 64
    assert checked["source_count"] == 3
    assert checked["official"] == "BLOCKED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_count", 4),
        ("source_sha256", "b" * 64),
        ("company", 11),
        ("period", 21),
    ],
)
def test_modified_evidence_rejected(field, value):
    data = json.loads(make_anchor(review()))
    data[field] = value

    with pytest.raises(HRInventoryBindingError):
        verify_anchor(
            json.dumps(
                data,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


def test_noncanonical_json_rejected():
    data = json.loads(make_anchor(review()))

    with pytest.raises(HRInventoryBindingError):
        verify_anchor(json.dumps(data, indent=2))


def test_unauthorized_mapping_rejected():
    data = review()
    data["d5"]["statutory_event_mapping"] = "APPROVED"

    with pytest.raises(HRInventoryBindingError):
        make_anchor(data)


def test_unauthorized_d6_applicability_rejected():
    data = review()
    data["d6"]["applicability"] = "CONFIRMED"

    with pytest.raises(HRInventoryBindingError):
        make_anchor(data)
