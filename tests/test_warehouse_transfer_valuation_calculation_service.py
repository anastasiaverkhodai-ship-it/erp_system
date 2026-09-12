from decimal import Decimal

import pytest

from app.models.company import InventoryValuationMethod
from app.services.warehouse_transfer_valuation_calculation_service import (
    WarehouseTransferFifoConsumptionSlice,
    WarehouseTransferSourceInventoryCost,
    WarehouseTransferValuationCalculationError,
    calculate_warehouse_transfer_valuation,
)


def fifo_source():
    return WarehouseTransferSourceInventoryCost(
        inventory_cost_entry_id=100,
        valuation_method=(
            InventoryValuationMethod.FIFO
        ),
        quantity=Decimal("5.0000"),
        unit_cost=Decimal("11.60000000"),
        valuation_amount=Decimal("58.00000000"),
    )


def fifo_slices():
    return (
        WarehouseTransferFifoConsumptionSlice(
            stock_lot_consumption_id=201,
            quantity=Decimal("3.0000"),
            unit_cost=Decimal("10.0000"),
        ),
        WarehouseTransferFifoConsumptionSlice(
            stock_lot_consumption_id=202,
            quantity=Decimal("2.0000"),
            unit_cost=Decimal("14.0000"),
        ),
    )


def ma_source(
    *,
    quantity="3.0000",
    unit_cost="3.33333333",
    valuation_amount="10.00000000",
):
    return WarehouseTransferSourceInventoryCost(
        inventory_cost_entry_id=300,
        valuation_method=(
            InventoryValuationMethod
            .WEIGHTED_AVERAGE_MOVING
        ),
        quantity=Decimal(quantity),
        unit_cost=Decimal(unit_cost),
        valuation_amount=Decimal(
            valuation_amount
        ),
    )


def test_fifo_preserves_two_exact_cost_layers():
    result = calculate_warehouse_transfer_valuation(
        source=fifo_source(),
        fifo_consumptions=fifo_slices(),
    )

    assert result.valuation_method == (
        InventoryValuationMethod.FIFO
    )

    assert len(result.layers) == 2

    assert [
        (
            layer.source_stock_lot_consumption_id,
            layer.quantity,
            layer.unit_cost,
            layer.valuation_amount,
        )
        for layer in result.layers
    ] == [
        (
            201,
            Decimal("3.0000"),
            Decimal("10.0000"),
            Decimal("30.00000000"),
        ),
        (
            202,
            Decimal("2.0000"),
            Decimal("14.0000"),
            Decimal("28.00000000"),
        ),
    ]


def test_fifo_quantity_conservation():
    result = calculate_warehouse_transfer_valuation(
        source=fifo_source(),
        fifo_consumptions=fifo_slices(),
    )

    assert result.layer_quantity == Decimal(
        "5.0000"
    )


def test_fifo_value_conservation():
    result = calculate_warehouse_transfer_valuation(
        source=fifo_source(),
        fifo_consumptions=fifo_slices(),
    )

    assert (
        result.layer_valuation_amount
        == Decimal("58.00000000")
    )


def test_fifo_does_not_average_layers():
    result = calculate_warehouse_transfer_valuation(
        source=fifo_source(),
        fifo_consumptions=fifo_slices(),
    )

    assert [
        layer.unit_cost
        for layer in result.layers
    ] == [
        Decimal("10.0000"),
        Decimal("14.0000"),
    ]


def test_fifo_requires_consumptions():
    with pytest.raises(
        WarehouseTransferValuationCalculationError,
        match="requires source",
    ):
        calculate_warehouse_transfer_valuation(
            source=fifo_source(),
        )


def test_fifo_rejects_quantity_mismatch():
    slices = (
        WarehouseTransferFifoConsumptionSlice(
            stock_lot_consumption_id=201,
            quantity=Decimal("3"),
            unit_cost=Decimal("10"),
        ),
    )

    with pytest.raises(
        WarehouseTransferValuationCalculationError,
        match="quantities do not equal",
    ):
        calculate_warehouse_transfer_valuation(
            source=fifo_source(),
            fifo_consumptions=slices,
        )


def test_fifo_rejects_value_mismatch():
    slices = (
        WarehouseTransferFifoConsumptionSlice(
            stock_lot_consumption_id=201,
            quantity=Decimal("3"),
            unit_cost=Decimal("10"),
        ),
        WarehouseTransferFifoConsumptionSlice(
            stock_lot_consumption_id=202,
            quantity=Decimal("2"),
            unit_cost=Decimal("15"),
        ),
    )

    with pytest.raises(
        WarehouseTransferValuationCalculationError,
        match="valuation does not equal",
    ):
        calculate_warehouse_transfer_valuation(
            source=fifo_source(),
            fifo_consumptions=slices,
        )


def test_fifo_rejects_duplicate_consumption():
    slices = (
        WarehouseTransferFifoConsumptionSlice(
            stock_lot_consumption_id=201,
            quantity=Decimal("3"),
            unit_cost=Decimal("10"),
        ),
        WarehouseTransferFifoConsumptionSlice(
            stock_lot_consumption_id=201,
            quantity=Decimal("2"),
            unit_cost=Decimal("14"),
        ),
    )

    with pytest.raises(
        WarehouseTransferValuationCalculationError,
        match="duplicate",
    ):
        calculate_warehouse_transfer_valuation(
            source=fifo_source(),
            fifo_consumptions=slices,
        )


def test_moving_average_emits_exactly_one_layer():
    source = ma_source()

    result = calculate_warehouse_transfer_valuation(
        source=source,
    )

    assert len(result.layers) == 1

    layer = result.layers[0]

    assert layer.valuation_method == (
        InventoryValuationMethod
        .WEIGHTED_AVERAGE_MOVING
    )
    assert layer.quantity == source.quantity
    assert layer.unit_cost == source.unit_cost
    assert (
        layer.valuation_amount
        == source.valuation_amount
    )
    assert (
        layer.source_inventory_cost_entry_id
        == source.inventory_cost_entry_id
    )
    assert (
        layer.source_stock_lot_consumption_id
        is None
    )


def test_moving_average_does_not_recompute_exact_ice_value():
    source = ma_source(
        quantity="3",
        unit_cost="3.33333333",
        valuation_amount="10.00000000",
    )

    assert (
        source.quantity
        * source.unit_cost
    ) == Decimal("9.99999999")

    result = calculate_warehouse_transfer_valuation(
        source=source,
    )

    assert (
        result.layer_valuation_amount
        == Decimal("10.00000000")
    )


def test_moving_average_rejects_fifo_consumptions():
    with pytest.raises(
        WarehouseTransferValuationCalculationError,
        match="must not contain FIFO",
    ):
        calculate_warehouse_transfer_valuation(
            source=ma_source(),
            fifo_consumptions=(
                WarehouseTransferFifoConsumptionSlice(
                    stock_lot_consumption_id=1,
                    quantity=Decimal("3"),
                    unit_cost=Decimal("3.3333"),
                ),
            ),
        )


def test_ma_detects_document_price_precision_loss():
    result = calculate_warehouse_transfer_valuation(
        source=ma_source(
            quantity="3",
            unit_cost="3.33333333",
            valuation_amount="10.00000000",
        ),
    )

    # Existing DocumentLine.price is Numeric(18,4).
    # 3 * 3.3333 = 9.9999, not 10.00000000.
    assert (
        result.requires_exact_receipt_value_path
        is True
    )


def test_ma_can_report_when_4dp_price_is_exact():
    result = calculate_warehouse_transfer_valuation(
        source=ma_source(
            quantity="2",
            unit_cost="5.25000000",
            valuation_amount="10.50000000",
        ),
    )

    assert (
        result.requires_exact_receipt_value_path
        is False
    )


@pytest.mark.parametrize(
    "field,value",
    (
        ("quantity", "NaN"),
        ("quantity", "Infinity"),
        ("unit_cost", "-1"),
        ("valuation_amount", "-1"),
    ),
)
def test_source_validation(
    field,
    value,
):
    values = {
        "quantity": "1",
        "unit_cost": "1",
        "valuation_amount": "1",
    }

    values[field] = value

    source = WarehouseTransferSourceInventoryCost(
        inventory_cost_entry_id=1,
        valuation_method=(
            InventoryValuationMethod
            .WEIGHTED_AVERAGE_MOVING
        ),
        quantity=Decimal(
            values["quantity"]
        ),
        unit_cost=Decimal(
            values["unit_cost"]
        ),
        valuation_amount=Decimal(
            values["valuation_amount"]
        ),
    )

    with pytest.raises(
        WarehouseTransferValuationCalculationError
    ):
        calculate_warehouse_transfer_valuation(
            source=source
        )


def test_bool_inventory_cost_entry_id_rejected():
    source = WarehouseTransferSourceInventoryCost(
        inventory_cost_entry_id=True,
        valuation_method=(
            InventoryValuationMethod
            .WEIGHTED_AVERAGE_MOVING
        ),
        quantity=Decimal("1"),
        unit_cost=Decimal("1"),
        valuation_amount=Decimal("1"),
    )

    with pytest.raises(
        WarehouseTransferValuationCalculationError,
        match="positive integer",
    ):
        calculate_warehouse_transfer_valuation(
            source=source
        )
