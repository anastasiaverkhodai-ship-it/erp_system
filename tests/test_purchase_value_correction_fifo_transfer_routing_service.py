from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.purchase_value_correction_fifo_transfer_routing_service import (
    PurchaseValueCorrectionFifoTransferRoutingIntegrityError,
    build_fifo_transfer_route_from_layer,
    load_fifo_transfer_route_for_consumption,
)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeDb:
    def __init__(self, rows):
        self.rows = list(rows)
        self.execute_calls = 0

    async def execute(self, statement):
        self.execute_calls += 1
        return _FakeResult(self.rows)


def _fifo_layer(
    *,
    layer_id=501,
    company_id=1,
    transfer_line_id=601,
    product_id=10,
    destination_warehouse_id=20,
    source_inventory_cost_entry_id=701,
    source_stock_lot_consumption_id=801,
    destination_receipt_document_id=901,
    destination_receipt_document_line_id=902,
    quantity=Decimal("3.0000"),
    unit_cost=Decimal("12.50000000"),
    valuation_amount=Decimal("37.50000000"),
    valuation_method="fifo",
):
    return SimpleNamespace(
        id=layer_id,
        company_id=company_id,
        transfer_line_id=transfer_line_id,
        product_id=product_id,
        destination_warehouse_id=destination_warehouse_id,
        valuation_method=valuation_method,
        quantity=quantity,
        unit_cost=unit_cost,
        valuation_amount=valuation_amount,
        source_inventory_cost_entry_id=(
            source_inventory_cost_entry_id
        ),
        source_stock_lot_consumption_id=(
            source_stock_lot_consumption_id
        ),
        destination_receipt_document_id=(
            destination_receipt_document_id
        ),
        destination_receipt_document_line_id=(
            destination_receipt_document_line_id
        ),
    )


def test_build_fifo_transfer_route_preserves_exact_provenance():
    layer = _fifo_layer()

    route = build_fifo_transfer_route_from_layer(
        layer
    )

    assert route.warehouse_transfer_valuation_layer_id == 501
    assert route.company_id == 1
    assert route.transfer_line_id == 601
    assert route.product_id == 10
    assert route.destination_warehouse_id == 20

    assert route.source_inventory_cost_entry_id == 701
    assert route.source_stock_lot_consumption_id == 801

    assert route.destination_receipt_document_id == 901
    assert route.destination_receipt_document_line_id == 902

    assert route.quantity == Decimal("3.0000")
    assert route.unit_cost == Decimal("12.50000000")
    assert route.valuation_amount == Decimal("37.50000000")


def test_build_fifo_transfer_route_rejects_non_fifo_layer():
    layer = _fifo_layer(
        valuation_method="weighted_average_moving"
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoTransferRoutingIntegrityError,
        match="FIFO",
    ):
        build_fifo_transfer_route_from_layer(
            layer
        )


def test_build_fifo_transfer_route_requires_source_consumption():
    layer = _fifo_layer(
        source_stock_lot_consumption_id=None
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoTransferRoutingIntegrityError,
        match="StockLotConsumption",
    ):
        build_fifo_transfer_route_from_layer(
            layer
        )


def test_build_fifo_transfer_route_requires_positive_quantity():
    layer = _fifo_layer(
        quantity=Decimal("0")
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoTransferRoutingIntegrityError,
        match="quantity",
    ):
        build_fifo_transfer_route_from_layer(
            layer
        )


def test_build_fifo_transfer_route_requires_exact_nonnegative_value():
    layer = _fifo_layer(
        valuation_amount=Decimal("-0.00000001")
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoTransferRoutingIntegrityError,
        match="valuation_amount",
    ):
        build_fifo_transfer_route_from_layer(
            layer
        )


@pytest.mark.asyncio
async def test_loader_returns_none_when_consumption_was_not_transferred():
    db = _FakeDb([])

    result = await load_fifo_transfer_route_for_consumption(
        db,
        company_id=1,
        stock_lot_consumption_id=801,
    )

    assert result is None
    assert db.execute_calls == 1


@pytest.mark.asyncio
async def test_loader_returns_exact_transfer_destination():
    db = _FakeDb(
        [
            _fifo_layer(),
        ]
    )

    result = await load_fifo_transfer_route_for_consumption(
        db,
        company_id=1,
        stock_lot_consumption_id=801,
    )

    assert result is not None

    assert (
        result.source_stock_lot_consumption_id
        == 801
    )

    assert (
        result.destination_receipt_document_line_id
        == 902
    )

    assert result.valuation_amount == Decimal(
        "37.50000000"
    )


@pytest.mark.asyncio
async def test_loader_rejects_ambiguous_transfer_provenance():
    db = _FakeDb(
        [
            _fifo_layer(
                layer_id=501,
            ),
            _fifo_layer(
                layer_id=502,
                destination_receipt_document_line_id=903,
            ),
        ]
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoTransferRoutingIntegrityError,
        match="multiple",
    ):
        await load_fifo_transfer_route_for_consumption(
            db,
            company_id=1,
            stock_lot_consumption_id=801,
        )
