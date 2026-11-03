from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal as D

import pytest

from app.services.payroll_severance_dated_tax_allocation import (
    ComponentAllocation,
    SeveranceAllocationError,
    build_dated_severance_allocation_review,
)


def row(component, total, ordinary, severance):
    return ComponentAllocation(
        component=component,
        statutory_total=total,
        ordinary_amount=ordinary,
        severance_amount=severance,
        evidence_reference="TEST-ONLY-NOT-APPROVED",
    )


def example():
    return [
        row(
            "personal_income_tax",
            D("2347.20"), D("547.20"), D("1800.00"),
        ),
        row(
            "military_levy",
            D("652.00"), D("152.00"), D("500.00"),
        ),
        row(
            "unified_social_contribution",
            D("228.80"), D("228.80"), D("0.00"),
        ),
    ]


def build(rows):
    return build_dated_severance_allocation_review(
        company_id=1,
        payroll_calculation_id=2,
        income_date=date(2026, 10, 30),
        currency_code="UAH",
        rows=rows,
    )


def test_explicit_allocation_reconciles():
    review = build(example())

    assert review.reconciled
    assert review.component(
        "personal_income_tax"
    ).severance_amount == D("1800.00")

    assert not review.independent_evidence_approved
    assert not review.financial_execution_authorized
    assert not review.statutory_correction_authorized


def test_arithmetic_mismatch_rejected():
    rows = example()
    rows[0] = row(
        "personal_income_tax",
        D("2347.20"), D("547.20"), D("1799.99"),
    )

    with pytest.raises(
        SeveranceAllocationError,
        match="do not reconcile",
    ):
        build(rows)


def test_duplicate_component_rejected():
    rows = example()
    rows[2] = rows[0]

    with pytest.raises(SeveranceAllocationError):
        build(rows)


def test_missing_component_rejected():
    with pytest.raises(SeveranceAllocationError):
        build(example()[:2])


@pytest.mark.parametrize(
    "bad",
    [
        D("-1.00"),
        D("1.001"),
        D("NaN"),
        D("Infinity"),
        True,
        1.5,
    ],
)
def test_invalid_money_rejected(bad):
    rows = example()
    rows[0] = row(
        "personal_income_tax",
        bad,
        D("547.20"),
        D("1800.00"),
    )
    with pytest.raises(SeveranceAllocationError):
        build(rows)


def test_immutable_review():
    review = build(example())
    with pytest.raises(FrozenInstanceError):
        review.financial_execution_authorized = True
