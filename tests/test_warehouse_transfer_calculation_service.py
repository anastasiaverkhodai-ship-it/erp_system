from decimal import Decimal

import pytest

from app.services.warehouse_transfer_calculation_service import (
    WarehouseTransferCalculationError,
    calculate_warehouse_transfer_target,
)


def test_transfer_builds_equal_opposite_warehouse_deltas():
    target = calculate_warehouse_transfer_target(
        company_id=1,
        product_id=10,
        source_warehouse_id=100,
        destination_warehouse_id=200,
        quantity=Decimal("7.5000"),
        source_quantity_on_hand=Decimal("10.0000"),
    )

    assert target.quantity == Decimal("7.5000")
    assert target.source_quantity_delta == Decimal("-7.5000")
    assert target.destination_quantity_delta == Decimal("7.5000")
    assert (
        target.source_quantity_delta
        + target.destination_quantity_delta
        == Decimal("0")
    )


def test_transfer_allows_exact_source_depletion():
    target = calculate_warehouse_transfer_target(
        company_id=1,
        product_id=10,
        source_warehouse_id=100,
        destination_warehouse_id=200,
        quantity=Decimal("10"),
        source_quantity_on_hand=Decimal("10"),
    )

    assert target.source_quantity_delta == Decimal("-10")
    assert target.destination_quantity_delta == Decimal("10")


@pytest.mark.parametrize(
    "quantity",
    [
        Decimal("0"),
        Decimal("-1"),
    ],
)
def test_transfer_rejects_nonpositive_quantity(quantity):
    with pytest.raises(
        WarehouseTransferCalculationError,
        match="quantity must be greater than zero",
    ):
        calculate_warehouse_transfer_target(
            company_id=1,
            product_id=10,
            source_warehouse_id=100,
            destination_warehouse_id=200,
            quantity=quantity,
            source_quantity_on_hand=Decimal("10"),
        )


def test_transfer_rejects_same_warehouse():
    with pytest.raises(
        WarehouseTransferCalculationError,
        match="warehouses must differ",
    ):
        calculate_warehouse_transfer_target(
            company_id=1,
            product_id=10,
            source_warehouse_id=100,
            destination_warehouse_id=100,
            quantity=Decimal("1"),
            source_quantity_on_hand=Decimal("10"),
        )


def test_transfer_rejects_insufficient_source_stock():
    with pytest.raises(
        WarehouseTransferCalculationError,
        match="exceeds source stock",
    ):
        calculate_warehouse_transfer_target(
            company_id=1,
            product_id=10,
            source_warehouse_id=100,
            destination_warehouse_id=200,
            quantity=Decimal("11"),
            source_quantity_on_hand=Decimal("10"),
        )


def test_transfer_rejects_negative_source_stock():
    with pytest.raises(
        WarehouseTransferCalculationError,
        match="cannot be negative",
    ):
        calculate_warehouse_transfer_target(
            company_id=1,
            product_id=10,
            source_warehouse_id=100,
            destination_warehouse_id=200,
            quantity=Decimal("1"),
            source_quantity_on_hand=Decimal("-1"),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("company_id", 0),
        ("product_id", 0),
        ("source_warehouse_id", 0),
        ("destination_warehouse_id", 0),
    ],
)
def test_transfer_rejects_nonpositive_ids(field, value):
    kwargs = {
        "company_id": 1,
        "product_id": 10,
        "source_warehouse_id": 100,
        "destination_warehouse_id": 200,
        "quantity": Decimal("1"),
        "source_quantity_on_hand": Decimal("10"),
    }
    kwargs[field] = value

    with pytest.raises(
        WarehouseTransferCalculationError,
        match=field,
    ):
        calculate_warehouse_transfer_target(**kwargs)
