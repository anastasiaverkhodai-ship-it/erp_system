from decimal import Decimal

import pytest

from app.services.sales_discount_service import (
    SalesDiscountError,
    calculate_sales_discount_snapshot,
)


def test_no_discount_preserves_effective_price():
    result = calculate_sales_discount_snapshot(
        base_price=Decimal("100"),
    )

    assert result.unit_price == Decimal("100.0000")
    assert result.base_unit_price is None
    assert result.discount_percent is None
    assert result.discount_amount_per_unit is None


def test_percentage_discount_snapshot():
    result = calculate_sales_discount_snapshot(
        base_price=Decimal("99.9900"),
        discount_percent=Decimal("7.5000"),
    )

    assert result.unit_price == Decimal("92.4908")
    assert result.base_unit_price == Decimal("99.9900")
    assert result.discount_percent == Decimal("7.5000")
    assert result.discount_amount_per_unit is None


def test_amount_discount_snapshot():
    result = calculate_sales_discount_snapshot(
        base_price=Decimal("100"),
        discount_amount_per_unit=Decimal("12.3456"),
    )

    assert result.unit_price == Decimal("87.6544")
    assert result.base_unit_price == Decimal("100.0000")
    assert result.discount_percent is None
    assert (
        result.discount_amount_per_unit
        == Decimal("12.3456")
    )


@pytest.mark.parametrize(
    ("percent", "expected"),
    [
        (
            Decimal("0"),
            Decimal("100.0000"),
        ),
        (
            Decimal("100"),
            Decimal("0.0000"),
        ),
    ],
)
def test_percentage_boundaries(
    percent,
    expected,
):
    result = calculate_sales_discount_snapshot(
        base_price=Decimal("100"),
        discount_percent=percent,
    )

    assert result.unit_price == expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (
            Decimal("0"),
            Decimal("100.0000"),
        ),
        (
            Decimal("100"),
            Decimal("0.0000"),
        ),
    ],
)
def test_amount_boundaries(
    amount,
    expected,
):
    result = calculate_sales_discount_snapshot(
        base_price=Decimal("100"),
        discount_amount_per_unit=amount,
    )

    assert result.unit_price == expected


def test_dual_discount_rejected():
    with pytest.raises(
        SalesDiscountError
    ):
        calculate_sales_discount_snapshot(
            base_price=Decimal("100"),
            discount_percent=Decimal("10"),
            discount_amount_per_unit=Decimal("5"),
        )


@pytest.mark.parametrize(
    "percent",
    [
        Decimal("-0.0001"),
        Decimal("100.0001"),
    ],
)
def test_invalid_percentage_rejected(
    percent,
):
    with pytest.raises(
        SalesDiscountError
    ):
        calculate_sales_discount_snapshot(
            base_price=Decimal("100"),
            discount_percent=percent,
        )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("-0.0001"),
        Decimal("100.0001"),
    ],
)
def test_invalid_amount_rejected(
    amount,
):
    with pytest.raises(
        SalesDiscountError
    ):
        calculate_sales_discount_snapshot(
            base_price=Decimal("100"),
            discount_amount_per_unit=amount,
        )


def test_percentage_rounding_is_deterministic():
    result = calculate_sales_discount_snapshot(
        base_price=Decimal("19.9999"),
        discount_percent=Decimal("12.3456"),
    )

    assert result.unit_price == Decimal("17.5308")
    assert result.unit_price.as_tuple().exponent == -4
