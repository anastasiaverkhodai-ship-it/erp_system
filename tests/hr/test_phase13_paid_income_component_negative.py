"""Source-scoped financial tests for 4DF paid-income components.

These are domain tests, not official statutory approval.
"""
from copy import deepcopy
from decimal import Decimal

import pytest

from app.services.payroll_paid_income_calculation import PaidIncomeError
from app.services.payroll_paid_income_components import (
    allocate_components,
    component_evidence,
    verify_component_evidence,
)


def source(
    line=1,
    amount="100.00",
    month="2026-09",
    kind="salary",
    funding="employer",
    company=1,
    contract=10,
    calculation=20,
):
    return {
        "company_id": company,
        "employment_contract_id": contract,
        "calculation_id": calculation,
        "calculation_line_id": line,
        "accrual_month": month,
        "income_type": kind,
        "funding": funding,
        "provenance": {},
        "gross": amount,
    }


def total(rows):
    return sum(
        (Decimal(item["gross"]) for item in rows),
        Decimal("0.00"),
    )


def test_mixed_salary_vacation_sick_and_severance():
    sources = [
        source(1, "100.00", "2026-09", "salary"),
        source(2, "30.00", "2026-09", "vacation_pay"),
        source(3, "20.00", "2026-09", "sick_pay",
               "insurer"),
        source(4, "40.00", "2026-09", "severance"),
    ]

    result = allocate_components(
        sources=sources,
        previous=[],
        gross="190.00",
    )

    assert total(result) == Decimal("190.00")
    assert len(result) == 4
    assert {
        (row["income_type"], row["funding"])
        for row in result
    } == {
        ("salary", "employer"),
        ("vacation_pay", "employer"),
        ("sick_pay", "insurer"),
        ("severance", "employer"),
    }


def test_multiple_accrual_months_fifo():
    sources = [
        source(2, "80.00", "2026-10"),
        source(1, "70.00", "2026-09"),
    ]

    result = allocate_components(
        sources=sources,
        previous=[],
        gross="100.00",
    )

    assert [r["accrual_month"] for r in result] == [
        "2026-09",
        "2026-10",
    ]
    assert [r["gross"] for r in result] == [
        "70.00",
        "30.00",
    ]


def test_partial_payment_residual():
    sources = [source(amount="100.00")]

    first = allocate_components(
        sources=sources,
        previous=[],
        gross="40.00",
    )

    second = allocate_components(
        sources=sources,
        previous=[{
            "settlement_id": "payment:1",
            "slices": first,
        }],
        gross="60.00",
    )

    assert total(first) == Decimal("40.00")
    assert total(second) == Decimal("60.00")


def test_overallocation_rejected():
    with pytest.raises(PaidIncomeError):
        allocate_components(
            sources=[source(amount="100.00")],
            previous=[],
            gross="100.01",
        )


def test_duplicate_component_rejected():
    same = source()

    with pytest.raises(PaidIncomeError):
        allocate_components(
            sources=[same, deepcopy(same)],
            previous=[],
            gross="10.00",
        )


@pytest.mark.parametrize("field,value", [
    ("company_id", 2),
    ("employment_contract_id", 11),
])
def test_mixed_identity_rejected(field, value):
    a = source(1)
    b = source(2)
    b[field] = value

    with pytest.raises(PaidIncomeError):
        allocate_components(
            sources=[a, b],
            previous=[],
            gross="10.00",
        )


@pytest.mark.parametrize("month", [
    "2026-00",
    "2026-13",
    "invalid",
])
def test_invalid_accrual_month_rejected(month):
    with pytest.raises(PaidIncomeError):
        allocate_components(
            sources=[source(month=month)],
            previous=[],
            gross="10.00",
        )


def test_reused_settlement_identity_rejected():
    part = allocate_components(
        sources=[source()],
        previous=[],
        gross="20.00",
    )

    with pytest.raises(PaidIncomeError):
        allocate_components(
            sources=[source()],
            previous=[
                {"settlement_id": "payment:1", "slices": part},
                {"settlement_id": "payment:1", "slices": part},
            ],
            gross="10.00",
        )


def test_previous_allocation_exceeds_balance():
    part = [source(amount="110.00")]

    with pytest.raises(PaidIncomeError):
        allocate_components(
            sources=[source(amount="100.00")],
            previous=[{
                "settlement_id": "payment:1",
                "slices": part,
            }],
            gross="1.00",
        )


def test_salary_only_does_not_consume_vacation():
    sources = [
        source(1, "30.00", kind="vacation_pay"),
        source(2, "100.00", kind="salary"),
    ]

    result = allocate_components(
        sources=sources,
        previous=[],
        gross="50.00",
        salary_only=True,
    )

    assert total(result) == Decimal("50.00")
    assert all(
        row["income_type"] == "salary"
        for row in result
    )


def test_salary_only_cannot_cover_non_salary_balance():
    with pytest.raises(PaidIncomeError):
        allocate_components(
            sources=[
                source(1, "20.00", kind="salary"),
                source(2, "80.00", kind="vacation_pay"),
            ],
            previous=[],
            gross="50.00",
            salary_only=True,
        )


def test_component_digest_deterministic():
    sources = [
        source(1, "100.00"),
        source(2, "20.00", kind="vacation_pay"),
    ]

    left = component_evidence(
        sources=sources,
        previous=[],
        gross="110.00",
    )

    right = component_evidence(
        sources=sources,
        previous=[],
        gross="110.00",
    )

    assert left == right
    assert verify_component_evidence(left) == left["body"]


def test_tampered_component_evidence_rejected():
    evidence = component_evidence(
        sources=[source()],
        previous=[],
        gross="40.00",
    )

    corrupted = deepcopy(evidence)
    corrupted["body"]["gross"] = "90.00"

    with pytest.raises(PaidIncomeError):
        verify_component_evidence(corrupted)


def test_negative_settlement_requires_reversal():
    with pytest.raises(PaidIncomeError):
        allocate_components(
            sources=[source()],
            previous=[],
            gross="-1.00",
        )
