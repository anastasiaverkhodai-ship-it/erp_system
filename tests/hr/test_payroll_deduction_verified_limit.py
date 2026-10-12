from decimal import Decimal

import pytest

from app.services.payroll_deduction_legal_classification import (
    CATEGORIES,
)
from app.services.payroll_deduction_verified_limit import (
    PayrollVerifiedLimitError,
    calculate_verified_deduction_limit,
)


def calculate(category, requested, withheld="0", net="10000"):
    return calculate_verified_deduction_limit(
        statutory_net=Decimal(net),
        requested_amount=Decimal(requested),
        already_withheld=Decimal(withheld),
        classification=CATEGORIES[category],
    )


@pytest.mark.parametrize(
    "category,requested,expected",
    [
        ("employer_statutory", "5000", "2000.00"),
        ("enforcement_other", "4000", "2000.00"),
        ("enforcement_special", "7000", "5000.00"),
        ("minor_child_alimony", "9000", "7000.00"),
    ],
)
def test_individual_limits(category, requested, expected):
    result = calculate(category, requested)
    assert result.permitted_amount == Decimal(expected)


def test_aggregate_limit():
    result = calculate(
        "enforcement_other",
        "2000",
        withheld="4500",
    )

    assert result.permitted_amount == Decimal("500.00")
    assert result.unapplied_amount == Decimal("1500.00")


def test_alimony_aggregate_limit():
    result = calculate(
        "minor_child_alimony",
        "3000",
        withheld="6000",
    )

    assert result.permitted_amount == Decimal("1000.00")


def test_request_below_limit():
    result = calculate("enforcement_other", "500")

    assert result.permitted_amount == Decimal("500.00")
    assert result.unapplied_amount == Decimal("0.00")


def test_zero_net():
    result = calculate(
        "employer_statutory",
        "100",
        net="0",
    )

    assert result.permitted_amount == Decimal("0.00")


@pytest.mark.parametrize(
    "net,requested,withheld",
    [
        ("-1", "100", "0"),
        ("10000", "-1", "0"),
        ("10000", "100", "-1"),
    ],
)
def test_negative_amounts(net, requested, withheld):
    with pytest.raises(PayrollVerifiedLimitError):
        calculate(
            "enforcement_other",
            requested,
            withheld=withheld,
            net=net,
        )


def test_invalid_classification():
    with pytest.raises(PayrollVerifiedLimitError):
        calculate_verified_deduction_limit(
            statutory_net=Decimal("10000"),
            requested_amount=Decimal("100"),
            already_withheld=Decimal("0"),
            classification=None,
        )
