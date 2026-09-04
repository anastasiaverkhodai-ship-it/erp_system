from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.sales_return_stock_restoration_service as service

from app.models.document import (
    DocumentType,
)
from app.models.stock_ledger import (
    StockMovementType,
)


D = date(
    2026,
    9,
    4,
)


def document():
    return SimpleNamespace(
        id=100,
        company_id=1,
        document_type=(
            DocumentType.RECEIPT
        ),
        document_date=D,
    )


def line():
    return SimpleNamespace(
        id=101,
        product_id=10,
        warehouse_id=20,
        quantity=Decimal(
            "3"
        ),
        price=Decimal(
            "999.0000"
        ),
    )


def trade_return():
    return SimpleNamespace(
        id=200,
        company_id=1,
        direction="sale",
        return_document_id=100,
        return_document_type="receipt",
        return_document_line_id=101,
        product_id=10,
        return_warehouse_id=20,
        return_date=D,
        returned_quantity=Decimal(
            "3"
        ),
    )


def cost_event(
    *,
    method="fifo",
    quantity="3",
    valuation="301.25900000",
    unit_cost="100.41966667",
):
    return SimpleNamespace(
        id=300,
        trade_return_event_id=200,
        inventory_cost_entry_id=50,
        restoration_date=D,
        valuation_method=method,
        restored_quantity=Decimal(
            quantity
        ),
        restored_valuation_amount=Decimal(
            valuation
        ),
        restored_cost_amount=Decimal(
            "301.26"
        ),
        aggregate_historical_unit_cost=Decimal(
            unit_cost
        ),
    )


def fifo_slice(
    *,
    row_id,
    quantity,
    unit_cost,
    valuation,
):
    return SimpleNamespace(
        id=row_id,
        fifo_consumption_id=1000 + row_id,
        stock_lot_id=2000 + row_id,
        restored_quantity=Decimal(
            quantity
        ),
        historical_unit_cost=Decimal(
            unit_cost
        ),
        restored_valuation_amount=Decimal(
            valuation
        ),
    )


def fifo_slices():
    return (
        fifo_slice(
            row_id=1,
            quantity="1",
            unit_cost="100.1234",
            valuation="100.12340000",
        ),
        fifo_slice(
            row_id=2,
            quantity="2",
            unit_cost="100.5678",
            valuation="201.13560000",
        ),
    )


def test_fifo_unit_cost_uses_native_four_decimal_scale():
    value = service._fifo_unit_cost(
        Decimal(
            "100.41966667"
        )
    )

    assert (
        value
        == Decimal(
            "100.4197"
        )
    )


def test_fifo_physical_unit_cost_is_not_exact_total_authority():
    exact_total = Decimal(
        "301.25900000"
    )

    physical_unit_cost = (
        service._fifo_unit_cost(
            Decimal(
                "100.41966667"
            )
        )
    )

    physical_recomputed = (
        Decimal(
            "3"
        )
        * physical_unit_cost
    )

    assert (
        physical_recomputed
        == Decimal(
            "301.2591"
        )
    )

    assert (
        physical_recomputed
        != exact_total
    )


def test_fifo_source_validation_uses_exact_slice_valuation():
    service.validate_sales_return_stock_restoration_source(
        document=document(),
        line=line(),
        trade_return_event=trade_return(),
        cost_event=cost_event(),
        fifo_slices=fifo_slices(),
    )


def test_fifo_source_validation_rejects_wrong_exact_total():
    bad = list(
        fifo_slices()
    )

    bad[1] = fifo_slice(
        row_id=2,
        quantity="2",
        unit_cost="100.5678",
        valuation="201.13550000",
    )

    with pytest.raises(
        service
        .SalesReturnStockRestorationDataIntegrityError,
        match="valuation",
    ):
        service.validate_sales_return_stock_restoration_source(
            document=document(),
            line=line(),
            trade_return_event=trade_return(),
            cost_event=cost_event(),
            fifo_slices=bad,
        )


def test_moving_average_rejects_fifo_provenance():
    with pytest.raises(
        service
        .SalesReturnStockRestorationDataIntegrityError,
        match="cannot contain FIFO",
    ):
        service.validate_sales_return_stock_restoration_source(
            document=document(),
            line=line(),
            trade_return_event=trade_return(),
            cost_event=cost_event(
                method="weighted_average_moving"
            ),
            fifo_slices=fifo_slices(),
        )


@pytest.mark.asyncio
async def test_fifo_restoration_ignores_return_line_price():
    class Result:
        def scalar_one_or_none(
            self,
        ):
            return None

    class DB:
        def __init__(
            self,
        ):
            self.added = []

        async def execute(
            self,
            statement,
        ):
            return Result()

        def add(
            self,
            value,
        ):
            self.added.append(
                value
            )

    db = DB()

    stock_lot = (
        await service.restore_sales_return_fifo_stock(
            db,
            document=document(),
            line=line(),
            cost_event=cost_event(),
        )
    )

    assert (
        stock_lot.unit_cost
        == Decimal(
            "100.4197"
        )
    )

    assert (
        stock_lot.unit_cost
        != Decimal(
            "999.0000"
        )
    )

    assert (
        stock_lot.original_quantity
        == Decimal(
            "3"
        )
    )

    assert (
        stock_lot.remaining_quantity
        == Decimal(
            "3"
        )
    )

    assert db.added == [
        stock_lot
    ]


@pytest.mark.asyncio
async def test_moving_average_restores_exact_historical_value(
    monkeypatch,
):
    class Result:
        def scalar_one_or_none(
            self,
        ):
            return None

    class DB:
        def __init__(
            self,
        ):
            self.added = []

        async def execute(
            self,
            statement,
        ):
            return Result()

        def add(
            self,
            value,
        ):
            self.added.append(
                value
            )

    balance = SimpleNamespace(
        quantity=Decimal(
            "2"
        ),
        inventory_value=Decimal(
            "200.00000000"
        ),
        average_unit_cost=Decimal(
            "100.00000000"
        ),
        updated_at=None,
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_moving_average_line_history",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_locked_moving_average_balance",
        AsyncMock(
            return_value=balance
        ),
    )

    db = DB()

    event = cost_event(
        method="weighted_average_moving",
        quantity="3",
        valuation="301.25900000",
        unit_cost="100.41966667",
    )

    movement = (
        await service.restore_sales_return_moving_average_stock(
            db,
            document=document(),
            line=line(),
            cost_event=event,
        )
    )

    assert (
        balance.quantity
        == Decimal(
            "5"
        )
    )

    assert (
        balance.inventory_value
        == Decimal(
            "501.25900000"
        )
    )

    assert (
        movement.movement_type
        == StockMovementType.RECEIPT
    )

    assert (
        movement.quantity_delta
        == Decimal(
            "3"
        )
    )

    assert (
        movement.value_delta
        == Decimal(
            "301.25900000"
        )
    )

    assert (
        movement.unit_cost
        == Decimal(
            "100.41966667"
        )
    )


@pytest.mark.asyncio
async def test_dispatch_fifo(
    monkeypatch,
):
    fifo = AsyncMock(
        return_value=object()
    )

    moving = AsyncMock(
        return_value=object()
    )

    monkeypatch.setattr(
        service,
        "restore_sales_return_fifo_stock",
        fifo,
    )

    monkeypatch.setattr(
        service,
        "restore_sales_return_moving_average_stock",
        moving,
    )

    result = (
        await service.restore_sales_return_physical_cost_state(
            object(),
            document=document(),
            line=line(),
            trade_return_event=trade_return(),
            cost_event=cost_event(),
            fifo_slices=fifo_slices(),
        )
    )

    assert result is fifo.return_value

    fifo.assert_awaited_once()

    moving.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_moving_average(
    monkeypatch,
):
    fifo = AsyncMock(
        return_value=object()
    )

    moving = AsyncMock(
        return_value=object()
    )

    monkeypatch.setattr(
        service,
        "restore_sales_return_fifo_stock",
        fifo,
    )

    monkeypatch.setattr(
        service,
        "restore_sales_return_moving_average_stock",
        moving,
    )

    result = (
        await service.restore_sales_return_physical_cost_state(
            object(),
            document=document(),
            line=line(),
            trade_return_event=trade_return(),
            cost_event=cost_event(
                method="weighted_average_moving"
            ),
            fifo_slices=(),
        )
    )

    assert result is moving.return_value

    moving.assert_awaited_once()

    fifo.assert_not_awaited()
