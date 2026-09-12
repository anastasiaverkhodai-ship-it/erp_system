from decimal import Decimal

import pytest

from app.services.inventory_count_calculation_service import (
    InventoryCountCalculationError,
    InventoryCountVarianceDirection,
    calculate_inventory_count_variance,
)


def test_count_zero_variance_is_observation_only():
    target = calculate_inventory_count_variance(
        company_id=1,
        product_id=10,
        warehouse_id=100,
        expected_quantity=Decimal("10"),
        counted_quantity=Decimal("10"),
    )

    assert target.variance_quantity == Decimal("0")
    assert target.direction == InventoryCountVarianceDirection.NONE
    assert target.requires_stock_adjustment is False


def test_count_positive_variance():
    target = calculate_inventory_count_variance(
        company_id=1,
        product_id=10,
        warehouse_id=100,
        expected_quantity=Decimal("10"),
        counted_quantity=Decimal("12.5000"),
    )

    assert target.variance_quantity == Decimal("2.5000")
    assert target.direction == InventoryCountVarianceDirection.INCREASE
    assert target.requires_stock_adjustment is True


def test_count_negative_variance():
    target = calculate_inventory_count_variance(
        company_id=1,
        product_id=10,
        warehouse_id=100,
        expected_quantity=Decimal("10"),
        counted_quantity=Decimal("7"),
    )

    assert target.variance_quantity == Decimal("-3")
    assert target.direction == InventoryCountVarianceDirection.DECREASE
    assert target.requires_stock_adjustment is True


def test_count_allows_zero_counted_quantity():
    target = calculate_inventory_count_variance(
        company_id=1,
        product_id=10,
        warehouse_id=100,
        expected_quantity=Decimal("10"),
        counted_quantity=Decimal("0"),
    )

    assert target.variance_quantity == Decimal("-10")
    assert target.direction == InventoryCountVarianceDirection.DECREASE


@pytest.mark.parametrize(
    ("expected", "counted", "message"),
    [
        (
            Decimal("-1"),
            Decimal("0"),
            "expected_quantity cannot be negative",
        ),
        (
            Decimal("0"),
            Decimal("-1"),
            "counted_quantity cannot be negative",
        ),
    ],
)
def test_count_rejects_negative_quantities(
    expected,
    counted,
    message,
):
    with pytest.raises(
        InventoryCountCalculationError,
        match=message,
    ):
        calculate_inventory_count_variance(
            company_id=1,
            product_id=10,
            warehouse_id=100,
            expected_quantity=expected,
            counted_quantity=counted,
        )


@pytest.mark.parametrize(
    "field",
    [
        "company_id",
        "product_id",
        "warehouse_id",
    ],
)
def test_count_rejects_nonpositive_ids(field):
    kwargs = {
        "company_id": 1,
        "product_id": 10,
        "warehouse_id": 100,
        "expected_quantity": Decimal("10"),
        "counted_quantity": Decimal("10"),
    }
    kwargs[field] = 0

    with pytest.raises(
        InventoryCountCalculationError,
        match=field,
    ):
        calculate_inventory_count_variance(**kwargs)
