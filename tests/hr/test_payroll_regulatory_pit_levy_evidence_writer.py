"""Independent PIT/levy source evidence integrity tests."""

from copy import deepcopy
import json

import pytest

from app.services.payroll_regulatory_pit_levy_evidence_writer import (
    PITLevyEvidenceError,
    make_anchor,
    verify_anchor,
)


def source():
    return {
        "payroll_calculation_id": 10,
        "employee_id": 20,
        "revision": 1,
        "issues": [],
        "currency_code": "UAH",
        "employee_withholding_amount": "230.00",
        "components": [
            {
                "source_line_id": 31,
                "component": "personal_income_tax",
                "amount": "180.00",
                "base_amount": "1000.00",
                "currency_code": "UAH",
            },
            {
                "source_line_id": 32,
                "component": "military_levy",
                "amount": "50.00",
                "base_amount": "1000.00",
                "currency_code": "UAH",
            },
            {
                "source_line_id": 33,
                "component": "unified_social_contribution",
                "amount": "220.00",
                "base_amount": "1000.00",
                "currency_code": "UAH",
            },
        ],
    }


def anchor():
    return make_anchor(
        company_id=1,
        payroll_period_id=2,
        source=source(),
    )


def test_roundtrip():
    checked = verify_anchor(anchor())

    assert checked["pit"] == "180.00"
    assert checked["levy"] == "50.00"
    assert checked["withheld"] == "230.00"
    assert len(checked["sha256"]) == 64


@pytest.mark.parametrize(
    "field, value",
    [
        ("pit", "181.00"),
        ("levy", "51.00"),
        ("withheld", "231.00"),
        ("company", 999),
        ("calculation", 999),
    ],
)
def test_tampered_evidence_rejected(field, value):
    changed = json.loads(anchor())
    changed[field] = value

    with pytest.raises(PITLevyEvidenceError):
        verify_anchor(
            json.dumps(
                changed,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


def test_changed_statutory_line_rejected():
    changed = json.loads(anchor())
    changed["lines"][0]["amount"] = "999.00"

    with pytest.raises(PITLevyEvidenceError):
        verify_anchor(
            json.dumps(
                changed,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


def test_missing_levy_source_rejected():
    changed = source()
    changed["components"] = [
        line for line in changed["components"]
        if line["component"] != "military_levy"
    ]

    with pytest.raises(PITLevyEvidenceError):
        make_anchor(
            company_id=1,
            payroll_period_id=2,
            source=changed,
        )


def test_duplicate_statutory_line_rejected():
    changed = source()
    changed["components"].append(
        deepcopy(changed["components"][0])
    )

    with pytest.raises(PITLevyEvidenceError):
        make_anchor(
            company_id=1,
            payroll_period_id=2,
            source=changed,
        )


def test_noncanonical_evidence_rejected():
    with pytest.raises(PITLevyEvidenceError):
        verify_anchor(
            json.dumps(json.loads(anchor()), indent=2)
        )


def test_aggregate_withholding_mismatch_rejected():
    changed = source()
    changed["employee_withholding_amount"] = "229.00"

    with pytest.raises(PITLevyEvidenceError):
        make_anchor(
            company_id=1,
            payroll_period_id=2,
            source=changed,
        )
