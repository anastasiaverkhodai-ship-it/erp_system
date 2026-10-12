"""Unit tests for conservative payroll deduction limit primitives."""

from decimal import Decimal

import pytest

from app.services.payroll_deduction_limit_policy import (
    PayrollDeductionLimitError,
    calculate_deduction_limit,
)


@pytest.mark.parametrize(
    ("percentage", "expected"),
    [
        ("20", "2000.00"),
        ("50", "5000.00"),
        ("70", "7000.00"),
    ],
)
def test_verified_aggregate_limits(percentage, expected):
    result = calculate_deduction_limit(
        statutory_net=Decimal("10000.00"),
        requested_amount=Decimal("9000.00"),
        already_withheld=Decimal("0.00"),
        verified_limit_percentage=Decimal(percentage),
    )

    assert result.permitted_amount == Decimal(expected)


def test_multiple_deductions_share_available_capacity():
    result = calculate_deduction_limit(
        statutory_net=Decimal("10000.00"),
        requested_amount=Decimal("1800.00"),
        already_withheld=Decimal("1500.00"),
        verified_limit_percentage=Decimal("20"),
    )

    assert result.maximum_total == Decimal("2000.00")
    assert result.available_limit == Decimal("500.00")
    assert result.permitted_amount == Decimal("500.00")
    assert result.unapplied_amount == Decimal("1300.00")


def test_existing_over_limit_fails_closed():
    with pytest.raises(
        PayrollDeductionLimitError,
        match="Existing deductions exceed",
    ):
        calculate_deduction_limit(
            statutory_net=Decimal("10000.00"),
            requested_amount=Decimal("100.00"),
            already_withheld=Decimal("2500.00"),
            verified_limit_percentage=Decimal("20"),
        )


def test_unverified_percentage_is_rejected():
    with pytest.raises(
        PayrollDeductionLimitError,
        match="Unsupported",
    ):
        calculate_deduction_limit(
            statutory_net=Decimal("10000.00"),
            requested_amount=Decimal("100.00"),
            already_withheld=Decimal("0.00"),
            verified_limit_percentage=Decimal("30"),
        )


@pytest.mark.parametrize(
    "value",
    [
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_invalid_money_is_rejected(value):
    with pytest.raises(PayrollDeductionLimitError):
        calculate_deduction_limit(
            statutory_net=value,
            requested_amount=Decimal("100.00"),
            already_withheld=Decimal("0.00"),
            verified_limit_percentage=Decimal("20"),
        )


def test_zero_net_has_zero_deduction_capacity():
    result = calculate_deduction_limit(
        statutory_net=Decimal("0.00"),
        requested_amount=Decimal("100.00"),
        already_withheld=Decimal("0.00"),
        verified_limit_percentage=Decimal("20"),
    )

    assert result.permitted_amount == Decimal("0.00")
    assert result.unapplied_amount == Decimal("100.00")
