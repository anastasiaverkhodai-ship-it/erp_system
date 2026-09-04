from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.sales_return_warehouse_quantity_service as service

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


def event(
    *,
    event_id=1,
    reversal_of_id=None,
    quantity="3",
    return_date=D1,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        direction="sale",
        original_fulfillment_id=10,
        original_trade_document_id=20,
        original_trade_document_line_id=21,
        original_fulfillment_line_id=30,
        product_id=100,
        return_document_id=200,
        return_document_type="receipt",
        return_document_line_id=201,
        return_warehouse_id=300,
        return_date=return_date,
        returned_quantity=Decimal(
            quantity
        ),
        reversal_of_id=reversal_of_id,
    )


def context(
    value,
):
    return service.SalesReturnWarehouseQuantityContext(
        event=value,
        document=SimpleNamespace(
            id=200,
            company_id=1,
            document_type=(
                DocumentType.RECEIPT
            ),
        ),
        line=SimpleNamespace(
            id=201,
            document_id=200,
            product_id=100,
            warehouse_id=300,
            quantity=Decimal(
                "3"
            ),
        ),
    )


def ledger(
    *,
    movement_id,
    movement_type,
    quantity,
):
    return SimpleNamespace(
        id=movement_id,
        company_id=1,
        document_id=200,
        document_line_id=201,
        product_id=100,
        warehouse_id=300,
        quantity=Decimal(
            quantity
        ),
        movement_type=movement_type,
    )


class DB:
    def __init__(
        self,
    ):
        self.added = []

    def add(
        self,
        value,
    ):
        self.added.append(
            value
        )


def test_empty_history_has_zero_active_quantity():
    value = event()

    assert (
        service._active_sales_return_quantity(
            context=context(
                value
            ),
            movements=(),
        )
        == Decimal(
            "0"
        )
    )


def test_receipt_history_has_active_return_quantity():
    value = event()

    movements = (
        ledger(
            movement_id=1,
            movement_type=(
                StockMovementType.RECEIPT
            ),
            quantity="3",
        ),
    )

    assert (
        service._active_sales_return_quantity(
            context=context(
                value
            ),
            movements=movements,
        )
        == Decimal(
            "3"
        )
    )


def test_receipt_then_reversal_has_zero_active_quantity():
    value = event()

    movements = (
        ledger(
            movement_id=1,
            movement_type=(
                StockMovementType.RECEIPT
            ),
            quantity="3",
        ),
        ledger(
            movement_id=2,
            movement_type=(
                StockMovementType.REVERSAL
            ),
            quantity="-3",
        ),
    )

    assert (
        service._active_sales_return_quantity(
            context=context(
                value
            ),
            movements=movements,
        )
        == Decimal(
            "0"
        )
    )


def test_original_reversal_replacement_history_is_active_again():
    value = event()

    movements = (
        ledger(
            movement_id=1,
            movement_type=(
                StockMovementType.RECEIPT
            ),
            quantity="3",
        ),
        ledger(
            movement_id=2,
            movement_type=(
                StockMovementType.REVERSAL
            ),
            quantity="-3",
        ),
        ledger(
            movement_id=3,
            movement_type=(
                StockMovementType.RECEIPT
            ),
            quantity="3",
        ),
    )

    assert (
        service._active_sales_return_quantity(
            context=context(
                value
            ),
            movements=movements,
        )
        == Decimal(
            "3"
        )
    )


def test_invalid_ledger_type_fails_closed():
    value = event()

    movements = (
        ledger(
            movement_id=1,
            movement_type=(
                StockMovementType.ISSUE
            ),
            quantity="-3",
        ),
    )

    with pytest.raises(
        service
        .SalesReturnWarehouseQuantityStateError,
        match="movement type",
    ):
        service._active_sales_return_quantity(
            context=context(
                value
            ),
            movements=movements,
        )


@pytest.mark.asyncio
async def test_original_posts_balance_and_receipt(
    monkeypatch,
):
    value = event()

    balance = SimpleNamespace(
        quantity=Decimal(
            "10"
        ),
        updated_at=None,
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_warehouse_quantity_context",
        AsyncMock(
            return_value=context(
                value
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_quantity_history",
        AsyncMock(
            return_value=()
        ),
    )

    period = AsyncMock()

    monkeypatch.setattr(
        service,
        "ensure_period_open",
        period,
    )

    lock_balance = AsyncMock(
        return_value=balance
    )

    monkeypatch.setattr(
        service,
        "get_locked_stock_balance",
        lock_balance,
    )

    db = DB()

    result = (
        await service.post_sales_return_warehouse_quantity(
            db,
            event=value,
        )
    )

    assert (
        balance.quantity
        == Decimal(
            "13"
        )
    )

    assert (
        result.quantity
        == Decimal(
            "3"
        )
    )

    assert (
        result.movement_type
        == StockMovementType.RECEIPT
    )

    assert (
        result.document_id
        == 200
    )

    assert (
        result.document_line_id
        == 201
    )

    assert db.added == [
        result
    ]

    period.assert_awaited_once_with(
        company_id=1,
        operation_date=D1,
        db=db,
    )


@pytest.mark.asyncio
async def test_duplicate_active_original_is_rejected(
    monkeypatch,
):
    value = event()

    active = (
        ledger(
            movement_id=1,
            movement_type=(
                StockMovementType.RECEIPT
            ),
            quantity="3",
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_warehouse_quantity_context",
        AsyncMock(
            return_value=context(
                value
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_quantity_history",
        AsyncMock(
            return_value=active
        ),
    )

    monkeypatch.setattr(
        service,
        "ensure_period_open",
        AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "get_locked_stock_balance",
        AsyncMock(),
    )

    with pytest.raises(
        service
        .SalesReturnWarehouseQuantityDuplicateError,
        match="already active",
    ):
        await service.post_sales_return_warehouse_quantity(
            DB(),
            event=value,
        )


@pytest.mark.asyncio
async def test_replacement_can_post_after_quantity_reversal(
    monkeypatch,
):
    value = event(
        event_id=3
    )

    history = (
        ledger(
            movement_id=1,
            movement_type=(
                StockMovementType.RECEIPT
            ),
            quantity="3",
        ),
        ledger(
            movement_id=2,
            movement_type=(
                StockMovementType.REVERSAL
            ),
            quantity="-3",
        ),
    )

    balance = SimpleNamespace(
        quantity=Decimal(
            "7"
        ),
        updated_at=None,
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_warehouse_quantity_context",
        AsyncMock(
            return_value=context(
                value
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_quantity_history",
        AsyncMock(
            return_value=history
        ),
    )

    monkeypatch.setattr(
        service,
        "ensure_period_open",
        AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "get_locked_stock_balance",
        AsyncMock(
            return_value=balance
        ),
    )

    db = DB()

    result = (
        await service.post_sales_return_warehouse_quantity(
            db,
            event=value,
        )
    )

    assert (
        balance.quantity
        == Decimal(
            "10"
        )
    )

    assert (
        result.movement_type
        == StockMovementType.RECEIPT
    )


@pytest.mark.asyncio
async def test_reversal_decreases_balance_and_creates_reversal(
    monkeypatch,
):
    reversal = event(
        event_id=2,
        reversal_of_id=1,
        return_date=D2,
    )

    original = event(
        event_id=1,
    )

    active = (
        ledger(
            movement_id=1,
            movement_type=(
                StockMovementType.RECEIPT
            ),
            quantity="3",
        ),
    )

    balance = SimpleNamespace(
        quantity=Decimal(
            "8"
        ),
        updated_at=None,
    )

    monkeypatch.setattr(
        service,
        "_load_reversed_trade_return_event",
        AsyncMock(
            return_value=original
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_warehouse_quantity_context",
        AsyncMock(
            return_value=context(
                reversal
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_quantity_history",
        AsyncMock(
            return_value=active
        ),
    )

    monkeypatch.setattr(
        service,
        "ensure_period_open",
        AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "get_locked_stock_balance",
        AsyncMock(
            return_value=balance
        ),
    )

    db = DB()

    result = (
        await service.reverse_sales_return_warehouse_quantity(
            db,
            reversal_event=reversal,
        )
    )

    assert (
        balance.quantity
        == Decimal(
            "5"
        )
    )

    assert (
        result.quantity
        == Decimal(
            "-3"
        )
    )

    assert (
        result.movement_type
        == StockMovementType.REVERSAL
    )

    assert (
        result.movement_date
        == D2
    )


@pytest.mark.asyncio
async def test_duplicate_quantity_reversal_is_rejected(
    monkeypatch,
):
    reversal = event(
        event_id=2,
        reversal_of_id=1,
        return_date=D2,
    )

    original = event(
        event_id=1
    )

    history = (
        ledger(
            movement_id=1,
            movement_type=(
                StockMovementType.RECEIPT
            ),
            quantity="3",
        ),
        ledger(
            movement_id=2,
            movement_type=(
                StockMovementType.REVERSAL
            ),
            quantity="-3",
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_reversed_trade_return_event",
        AsyncMock(
            return_value=original
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_warehouse_quantity_context",
        AsyncMock(
            return_value=context(
                reversal
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_quantity_history",
        AsyncMock(
            return_value=history
        ),
    )

    monkeypatch.setattr(
        service,
        "ensure_period_open",
        AsyncMock(),
    )

    with pytest.raises(
        service
        .SalesReturnWarehouseQuantityDuplicateError,
        match="already reversed",
    ):
        await service.reverse_sales_return_warehouse_quantity(
            DB(),
            reversal_event=reversal,
        )


@pytest.mark.asyncio
async def test_reversal_protects_against_negative_stock(
    monkeypatch,
):
    reversal = event(
        event_id=2,
        reversal_of_id=1,
        return_date=D2,
    )

    original = event(
        event_id=1
    )

    history = (
        ledger(
            movement_id=1,
            movement_type=(
                StockMovementType.RECEIPT
            ),
            quantity="3",
        ),
    )

    balance = SimpleNamespace(
        quantity=Decimal(
            "2"
        ),
        updated_at=None,
    )

    monkeypatch.setattr(
        service,
        "_load_reversed_trade_return_event",
        AsyncMock(
            return_value=original
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_warehouse_quantity_context",
        AsyncMock(
            return_value=context(
                reversal
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_quantity_history",
        AsyncMock(
            return_value=history
        ),
    )

    monkeypatch.setattr(
        service,
        "ensure_period_open",
        AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "get_locked_stock_balance",
        AsyncMock(
            return_value=balance
        ),
    )

    with pytest.raises(
        service
        .SalesReturnWarehouseQuantityStateError,
        match="negative",
    ):
        await service.reverse_sales_return_warehouse_quantity(
            DB(),
            reversal_event=reversal,
        )


@pytest.mark.asyncio
async def test_dispatches_original_and_reversal(
    monkeypatch,
):
    post = AsyncMock(
        return_value=object()
    )

    reverse = AsyncMock(
        return_value=object()
    )

    monkeypatch.setattr(
        service,
        "post_sales_return_warehouse_quantity",
        post,
    )

    monkeypatch.setattr(
        service,
        "reverse_sales_return_warehouse_quantity",
        reverse,
    )

    original = event(
        event_id=1
    )

    reversal = event(
        event_id=2,
        reversal_of_id=1,
    )

    original_result = (
        await service.apply_sales_return_warehouse_quantity_event(
            object(),
            event=original,
        )
    )

    reversal_result = (
        await service.apply_sales_return_warehouse_quantity_event(
            object(),
            event=reversal,
        )
    )

    assert (
        original_result
        is post.return_value
    )

    assert (
        reversal_result
        is reverse.return_value
    )

    post.assert_awaited_once()

    reverse.assert_awaited_once()


def test_non_sale_event_is_rejected():
    value = event()

    value.direction = "purchase"

    with pytest.raises(
        service
        .SalesReturnWarehouseQuantitySourceError,
        match="sales",
    ):
        service._validate_event_identity(
            value
        )


def test_non_receipt_event_is_rejected():
    value = event()

    value.return_document_type = "issue"

    with pytest.raises(
        service
        .SalesReturnWarehouseQuantitySourceError,
        match="RECEIPT",
    ):
        service._validate_event_identity(
            value
        )
