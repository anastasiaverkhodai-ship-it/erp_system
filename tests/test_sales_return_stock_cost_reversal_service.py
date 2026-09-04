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


D1 = date(
    2026,
    9,
    1,
)

D2 = date(
    2026,
    9,
    2,
)


def document():
    return SimpleNamespace(
        id=100,
        company_id=1,
        document_type=(
            DocumentType.RECEIPT
        ),
        document_date=D1,
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
            "999"
        ),
    )


def original_cost(
    *,
    method="fifo",
    amount="301.25900000",
    unit_cost="100.41966667",
):
    return SimpleNamespace(
        id=300,
        company_id=1,
        trade_return_event_id=200,
        inventory_cost_entry_id=50,
        restoration_date=D1,
        valuation_method=method,
        restored_quantity=Decimal(
            "3"
        ),
        restored_valuation_amount=Decimal(
            amount
        ),
        restored_cost_amount=Decimal(
            "301.26"
        ),
        aggregate_historical_unit_cost=Decimal(
            unit_cost
        ),
        reversal_of_id=None,
    )


def reversal_cost(
    *,
    method="fifo",
    amount="301.25900000",
    unit_cost="100.41966667",
):
    value = original_cost(
        method=method,
        amount=amount,
        unit_cost=unit_cost,
    )

    value.id = 301
    value.restoration_date = D2
    value.reversal_of_id = 300

    return value


class ScalarResult:
    def __init__(
        self,
        value,
    ):
        self.value = value

    def scalar_one_or_none(
        self,
    ):
        return self.value


class DB:
    def __init__(
        self,
        *values,
    ):
        self.values = list(
            values
        )

        self.added = []

    async def execute(
        self,
        statement,
    ):
        if not self.values:
            raise AssertionError(
                "Unexpected DB execute"
            )

        return ScalarResult(
            self.values.pop(
                0
            )
        )

    def add(
        self,
        value,
    ):
        self.added.append(
            value
        )


@pytest.mark.asyncio
async def test_fifo_reversal_deactivates_unconsumed_return_lot():
    lot = SimpleNamespace(
        id=1,
        company_id=1,
        product_id=10,
        warehouse_id=20,
        source_document_id=100,
        source_document_line_id=101,
        original_quantity=Decimal(
            "3"
        ),
        remaining_quantity=Decimal(
            "3"
        ),
        unit_cost=Decimal(
            "100.4197"
        ),
    )

    db = DB(
        lot
    )

    result = (
        await service.reverse_sales_return_fifo_cost_state(
            db,
            document=document(),
            line=line(),
            reversal_event=reversal_cost(),
        )
    )

    assert result is lot

    assert (
        lot.remaining_quantity
        == Decimal(
            "0"
        )
    )


@pytest.mark.asyncio
async def test_fifo_reversal_rejects_consumed_return_lot():
    lot = SimpleNamespace(
        id=1,
        company_id=1,
        product_id=10,
        warehouse_id=20,
        source_document_id=100,
        source_document_line_id=101,
        original_quantity=Decimal(
            "3"
        ),
        remaining_quantity=Decimal(
            "2"
        ),
        unit_cost=Decimal(
            "100.4197"
        ),
    )

    db = DB(
        lot
    )

    with pytest.raises(
        service
        .SalesReturnStockRestorationDataIntegrityError,
        match="consumed",
    ):
        await service.reverse_sales_return_fifo_cost_state(
            db,
            document=document(),
            line=line(),
            reversal_event=reversal_cost(),
        )


@pytest.mark.asyncio
async def test_fifo_replacement_reactivates_same_lot():
    lot = SimpleNamespace(
        id=1,
        company_id=1,
        product_id=10,
        warehouse_id=20,
        source_document_id=100,
        source_document_line_id=101,
        received_date=D1,
        original_quantity=Decimal(
            "3"
        ),
        remaining_quantity=Decimal(
            "0"
        ),
        unit_cost=Decimal(
            "100.4197"
        ),
    )

    db = DB(
        lot
    )

    replacement = original_cost(
        unit_cost="110.12345678",
    )

    result = (
        await service.restore_sales_return_fifo_stock(
            db,
            document=document(),
            line=line(),
            cost_event=replacement,
        )
    )

    assert result is lot

    assert (
        lot.remaining_quantity
        == Decimal(
            "3"
        )
    )

    assert (
        lot.unit_cost
        == Decimal(
            "110.1235"
        )
    )

    assert db.added == []


def movement(
    *,
    movement_id,
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=movement_id,
        reversal_of_id=(
            reversal_of_id
        ),
    )


def test_active_moving_average_history_original():
    original = movement(
        movement_id=1
    )

    assert (
        service._active_sales_return_moving_average_originals(
            (
                original,
            )
        )
        == (
            original,
        )
    )


def test_active_moving_average_history_reversal_removes_original():
    original = movement(
        movement_id=1
    )

    reversal = movement(
        movement_id=2,
        reversal_of_id=1,
    )

    assert (
        service._active_sales_return_moving_average_originals(
            (
                original,
                reversal,
            )
        )
        == ()
    )


def test_active_moving_average_history_replacement_becomes_active():
    original = movement(
        movement_id=1
    )

    reversal = movement(
        movement_id=2,
        reversal_of_id=1,
    )

    replacement = movement(
        movement_id=3
    )

    assert (
        service._active_sales_return_moving_average_originals(
            (
                original,
                reversal,
                replacement,
            )
        )
        == (
            replacement,
        )
    )


@pytest.mark.asyncio
async def test_moving_average_reversal_restores_previous_balance(
    monkeypatch,
):
    active = SimpleNamespace(
        id=20,
        company_id=1,
        document_id=100,
        document_line_id=101,
        product_id=10,
        warehouse_id=20,
        movement_type=(
            StockMovementType.RECEIPT
        ),
        quantity_delta=Decimal(
            "3"
        ),
        value_delta=Decimal(
            "301.25900000"
        ),
        unit_cost=Decimal(
            "100.41966667"
        ),
        balance_quantity_after=Decimal(
            "8"
        ),
        balance_value_after=Decimal(
            "801.25900000"
        ),
        average_unit_cost_after=Decimal(
            "100.15737500"
        ),
        reversal_of_id=None,
    )

    previous = SimpleNamespace(
        id=19,
        balance_quantity_after=Decimal(
            "5"
        ),
        balance_value_after=Decimal(
            "500.00000000"
        ),
        average_unit_cost_after=Decimal(
            "100.00000000"
        ),
    )

    balance = SimpleNamespace(
        quantity=Decimal(
            "8"
        ),
        inventory_value=Decimal(
            "801.25900000"
        ),
        average_unit_cost=Decimal(
            "100.15737500"
        ),
        updated_at=None,
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_moving_average_line_history",
        AsyncMock(
            return_value=(
                active,
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "get_locked_moving_average_balance",
        AsyncMock(
            return_value=balance
        ),
    )

    db = DB(
        active,
        previous,
    )

    result = (
        await service.reverse_sales_return_moving_average_cost_state(
            db,
            document=document(),
            line=line(),
            reversal_event=reversal_cost(
                method="weighted_average_moving"
            ),
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
            "500.00000000"
        )
    )

    assert (
        result.movement_type
        == StockMovementType.REVERSAL
    )

    assert (
        result.quantity_delta
        == Decimal(
            "-3"
        )
    )

    assert (
        result.value_delta
        == Decimal(
            "-301.25900000"
        )
    )

    assert (
        result.reversal_of_id
        == 20
    )

    assert db.added == [
        result
    ]


@pytest.mark.asyncio
async def test_moving_average_reversal_rejects_later_inventory_movement(
    monkeypatch,
):
    active = SimpleNamespace(
        id=20,
        company_id=1,
        document_id=100,
        document_line_id=101,
        product_id=10,
        warehouse_id=20,
        quantity_delta=Decimal(
            "3"
        ),
        value_delta=Decimal(
            "301.25900000"
        ),
        unit_cost=Decimal(
            "100.41966667"
        ),
        balance_quantity_after=Decimal(
            "3"
        ),
        balance_value_after=Decimal(
            "301.25900000"
        ),
        average_unit_cost_after=Decimal(
            "100.41966667"
        ),
        reversal_of_id=None,
    )

    later = SimpleNamespace(
        id=21
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_moving_average_line_history",
        AsyncMock(
            return_value=(
                active,
            )
        ),
    )

    db = DB(
        later
    )

    with pytest.raises(
        service
        .SalesReturnStockRestorationDataIntegrityError,
        match="later inventory",
    ):
        await service.reverse_sales_return_moving_average_cost_state(
            db,
            document=document(),
            line=line(),
            reversal_event=reversal_cost(
                method="weighted_average_moving"
            ),
        )


@pytest.mark.asyncio
async def test_moving_average_replacement_allowed_after_reversal(
    monkeypatch,
):
    original = movement(
        movement_id=1
    )

    reversal = movement(
        movement_id=2,
        reversal_of_id=1,
    )

    balance = SimpleNamespace(
        quantity=Decimal(
            "5"
        ),
        inventory_value=Decimal(
            "500.00000000"
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
            return_value=(
                original,
                reversal,
            )
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

    result = (
        await service.restore_sales_return_moving_average_stock(
            db,
            document=document(),
            line=line(),
            cost_event=original_cost(
                method="weighted_average_moving",
                unit_cost="110.00000000",
                amount="330.00000000",
            ),
        )
    )

    assert (
        result.quantity_delta
        == Decimal(
            "3"
        )
    )

    assert (
        result.value_delta
        == Decimal(
            "330.00000000"
        )
    )

    assert (
        balance.quantity
        == Decimal(
            "8"
        )
    )

    assert (
        balance.inventory_value
        == Decimal(
            "830.00000000"
        )
    )


@pytest.mark.asyncio
async def test_physical_reversal_dispatches_fifo(
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
        "reverse_sales_return_fifo_cost_state",
        fifo,
    )

    monkeypatch.setattr(
        service,
        "reverse_sales_return_moving_average_cost_state",
        moving,
    )

    event = reversal_cost(
        method="fifo"
    )

    result = (
        await service.reverse_sales_return_physical_cost_state(
            object(),
            document=document(),
            line=line(),
            reversal_event=event,
        )
    )

    assert result is fifo.return_value

    fifo.assert_awaited_once()

    moving.assert_not_awaited()


@pytest.mark.asyncio
async def test_physical_reversal_dispatches_moving_average(
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
        "reverse_sales_return_fifo_cost_state",
        fifo,
    )

    monkeypatch.setattr(
        service,
        "reverse_sales_return_moving_average_cost_state",
        moving,
    )

    event = reversal_cost(
        method="weighted_average_moving"
    )

    result = (
        await service.reverse_sales_return_physical_cost_state(
            object(),
            document=document(),
            line=line(),
            reversal_event=event,
        )
    )

    assert result is moving.return_value

    moving.assert_awaited_once()

    fifo.assert_not_awaited()
