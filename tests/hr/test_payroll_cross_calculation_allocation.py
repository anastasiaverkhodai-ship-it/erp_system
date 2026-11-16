from copy import deepcopy
from decimal import Decimal

import pytest

from app.services.payroll_cross_calculation_allocation import (
    CrossCalculationAllocationError as Error,
    review_cross_calculation_payment as review,
    verify_cross_calculation_review as verify,
)


def source(
    calc,
    amount,
    due="2026-10-07",
    company=1,
    employee=2,
):
    return {
        "company_id": company,
        "employee_id": employee,
        "calculation_id": calc,
        "payable_amount": amount,
        "due_date": due,
    }


def build(**kwargs):
    arguments = {
        "payment_reference": "bank:2026:001",
        "company_id": 1,
        "employee_id": 2,
        "payment_amount": "150.00",
        "sources": [
            source(11, "100.00"),
            source(12, "200.00", "2026-10-08"),
        ],
    }
    arguments.update(kwargs)
    return review(**arguments)


def values(record):
    return [
        (
            row["calculation_id"],
            row["allocated_amount"],
        )
        for row in record["body"]["allocations"]
    ]


def test_two_calculations_one_payment():
    assert values(build()) == [
        (11, "100.00"),
        (12, "50.00"),
    ]


def test_oldest_due_date_first():
    result = build(sources=[
        source(11, "100.00", "2026-10-20"),
        source(12, "200.00", "2026-10-01"),
    ])
    assert values(result) == [(12, "150.00")]


def test_existing_settlement_reduces_balance():
    result = build(previous_settlements=[
        {
            "settlement_id": "earlier:1",
            "calculation_id": 11,
            "allocated_amount": "80.00",
        }
    ])
    assert values(result) == [
        (11, "20.00"),
        (12, "130.00"),
    ]


def test_remainder_is_exact():
    result = build(
        payment_amount="0.03",
        sources=[
            source(11, "0.01"),
            source(12, "0.02"),
        ],
    )
    assert values(result) == [
        (11, "0.01"),
        (12, "0.02"),
    ]


@pytest.mark.parametrize("amount", [
    "300.01", "9999.99",
])
def test_overpayment_rejected(amount):
    with pytest.raises(Error):
        build(payment_amount=amount)


@pytest.mark.parametrize("amount", [
    "-1.00", "0.00", "NaN",
    "Infinity", "1.001", 1.0,
])
def test_invalid_payment_amount_rejected(amount):
    with pytest.raises(Error):
        build(payment_amount=amount)


def test_cross_employee_rejected():
    with pytest.raises(Error):
        build(sources=[
            source(11, "100.00"),
            source(12, "200.00", employee=3),
        ])


def test_cross_company_rejected():
    with pytest.raises(Error):
        build(sources=[
            source(11, "100.00"),
            source(12, "200.00", company=3),
        ])


def test_duplicate_calculation_rejected():
    with pytest.raises(Error):
        build(sources=[
            source(11, "100.00"),
            source(11, "200.00"),
        ])


def test_previous_settlement_overallocation_rejected():
    with pytest.raises(Error):
        build(previous_settlements=[
            {
                "settlement_id": "earlier",
                "calculation_id": 11,
                "allocated_amount": "101.00",
            }
        ])


def test_duplicate_previous_settlement_rejected():
    previous = [
        {
            "settlement_id": "earlier",
            "calculation_id": 11,
            "allocated_amount": "20.00",
        }
    ]
    with pytest.raises(Error):
        build(previous_settlements=previous + previous)


def test_unrelated_previous_settlement_rejected():
    with pytest.raises(Error):
        build(previous_settlements=[{
            "settlement_id": "earlier",
            "calculation_id": 99,
            "allocated_amount": "20.00",
        }])


def test_payment_identity_reuse_rejected():
    with pytest.raises(Error):
        build(previous_settlements=[{
            "settlement_id": "bank:2026:001",
            "calculation_id": 11,
            "allocated_amount": "20.00",
        }])


def test_currency_guard():
    with pytest.raises(Error):
        build(currency="EUR")


def test_cannot_use_single_calculation():
    with pytest.raises(Error):
        build(sources=[source(11, "200.00")])


def test_review_does_not_authorize_posting():
    result = build()
    assert result["body"]["review_status"] == "REVIEW_ONLY"
    assert result["body"]["posting_authorized"] is False
    assert result["body"]["official_generation"] == "BLOCKED"


def test_review_is_deterministic():
    assert build() == build()
    assert verify(build()) == build()["body"]


def test_tampering_rejected():
    result = build()
    tampered = deepcopy(result)
    tampered["body"]["payment_amount"] = "500.00"
    with pytest.raises(Error):
        verify(tampered)


def test_forged_valid_digest_cannot_change_allocation():
    from app.services.payroll_cross_calculation_allocation import (
        _digest,
    )
    forged = deepcopy(build())
    forged["body"]["allocations"][0]["allocated_amount"] = "90.00"
    forged["sha256"] = _digest(forged["body"])
    with pytest.raises(Error):
        verify(forged)
