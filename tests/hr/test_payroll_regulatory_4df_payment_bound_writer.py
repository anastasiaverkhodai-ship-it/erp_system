"""Canonical 4DF payment evidence integrity tests."""

from copy import deepcopy
from datetime import date
import json

import pytest

from app.services.payroll_regulatory_4df_payment_bound_writer import (
    FourDFPaymentBindingError,
    build_anchor,
    verify_anchor,
)


def allocation():
    return {
        "payroll_calculation_id": 10,
        "_verified_employee_id": 20,
        "payment_date": date(2026, 9, 30),
        "payroll_disbursement_ids": [30],
        "tax_remittance_line_ids": [40],
        "paid_amount": "100.00",
        "pit_remitted": "18.00",
        "levy_remitted": "5.00",
        "payment_evidence_reference": "payroll_disbursements:30",
        "tax_remittance_evidence_reference":
            "payroll_tax_remittance_lines:40",
    }


def test_roundtrip():
    encoded = build_anchor(10, 20, [allocation()])
    result = verify_anchor(encoded)

    assert result["c"] == 10
    assert result["e"] == 20
    assert len(result["sha256"]) == 64


@pytest.mark.parametrize("field", ["a", "i", "l", "d"])
def test_tampering_blocked(field):
    original = json.loads(
        build_anchor(10, 20, [allocation()])
    )
    original["items"][0][field] = (
        "2026-09-29" if field == "d" else "999.00"
    )

    with pytest.raises(FourDFPaymentBindingError):
        verify_anchor(
            json.dumps(
                original,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


def test_duplicate_payment_rejected():
    item = allocation()

    with pytest.raises(FourDFPaymentBindingError):
        build_anchor(10, 20, [item, deepcopy(item)])


def test_wrong_employee_rejected():
    with pytest.raises(FourDFPaymentBindingError):
        build_anchor(10, 999, [allocation()])


def test_empty_evidence_rejected():
    with pytest.raises(FourDFPaymentBindingError):
        verify_anchor("")
