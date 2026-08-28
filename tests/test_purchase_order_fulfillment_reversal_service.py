from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.trade_fulfillment_service as service
from app.models.document import (
    DocumentStatus,
    DocumentType,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)
from app.services.trade_fulfillment_service import (
    PurchaseOrderFulfillmentReversalStateError,
    PurchaseOrderFulfillmentReversalStatusError,
    PurchaseOrderFulfillmentTypeError,
    execute_purchase_order_fulfillment_reversal,
    validate_purchase_order_fulfillment_reversal_state,
)


def make_line(
    *,
    line_id=11,
    quantity="5.0000",
):
    return SimpleNamespace(
        id=line_id,
        line_number=1,
        product_id=101,
        warehouse_id=201,
        quantity=Decimal(quantity),
        unit_price=Decimal("125.5000"),
    )


def make_order(
    *,
    status=TradeDocumentStatus.PARTIALLY_FULFILLED,
    direction=TradeDirection.PURCHASE,
    kind=TradeDocumentKind.ORDER,
    quantity="5.0000",
):
    line = make_line(
        quantity=quantity
    )

    return (
        SimpleNamespace(
            id=50,
            company_id=1,
            direction=direction,
            kind=kind,
            status=status,
            lines=[
                line
            ],
        ),
        line,
    )


def make_fulfillment():
    return SimpleNamespace(
        id=20,
        company_id=1,
        trade_document_id=50,
        warehouse_document_id=30,
        warehouse_document_type=(
            DocumentType.RECEIPT.value
        ),
    )


def make_mapping(
    *,
    line,
    quantity,
):
    return SimpleNamespace(
        id=40,
        company_id=1,
        fulfillment_id=20,
        trade_document_id=50,
        trade_document_line_id=line.id,
        warehouse_document_id=30,
        warehouse_document_line_id=31,
        product_id=line.product_id,
        warehouse_id=line.warehouse_id,
        quantity=Decimal(quantity),
    )


@pytest.mark.parametrize(
    "status",
    [
        TradeDocumentStatus.PARTIALLY_FULFILLED,
        TradeDocumentStatus.FULFILLED,
    ],
)
def test_purchase_reversal_accepts_active_fulfillment_states(
    status,
):
    order, _ = make_order(
        status=status
    )

    validate_purchase_order_fulfillment_reversal_state(
        order
    )


def test_purchase_reversal_rejects_sale():
    order, _ = make_order(
        direction=TradeDirection.SALE
    )

    with pytest.raises(
        PurchaseOrderFulfillmentTypeError
    ):
        validate_purchase_order_fulfillment_reversal_state(
            order
        )


@pytest.mark.parametrize(
    "status",
    [
        TradeDocumentStatus.DRAFT,
        TradeDocumentStatus.CONFIRMED,
        TradeDocumentStatus.CANCELLED,
    ],
)
def test_purchase_reversal_rejects_invalid_status(
    status,
):
    order, _ = make_order(
        status=status
    )

    with pytest.raises(
        PurchaseOrderFulfillmentReversalStatusError
    ):
        validate_purchase_order_fulfillment_reversal_state(
            order
        )


@pytest.mark.asyncio
async def test_purchase_reversal_partial_to_confirmed_without_reservations(
    monkeypatch,
):
    order, line = make_order(
        status=(
            TradeDocumentStatus
            .PARTIALLY_FULFILLED
        )
    )

    fulfillment = make_fulfillment()

    mapping = make_mapping(
        line=line,
        quantity="2.0000",
    )

    db = SimpleNamespace(
        flush=AsyncMock()
    )

    monkeypatch.setattr(
        service,
        "get_locked_purchase_order",
        AsyncMock(
            return_value=order
        ),
    )

    monkeypatch.setattr(
        service,
        "get_locked_purchase_trade_fulfillment",
        AsyncMock(
            return_value=fulfillment
        ),
    )

    monkeypatch.setattr(
        service,
        "get_trade_fulfillment_lines_for_reversal",
        AsyncMock(
            return_value=(
                mapping,
            )
        ),
    )

    fulfilled = AsyncMock(
        side_effect=[
            {
                line.id: Decimal("2.0000"),
            },
            {
                line.id: Decimal("0.0000"),
            },
        ]
    )

    monkeypatch.setattr(
        service,
        "get_persisted_fulfilled_quantities",
        fulfilled,
    )

    reverse = AsyncMock(
        return_value=SimpleNamespace(
            id=30,
            document_type=(
                DocumentType.RECEIPT
            ),
            status=(
                DocumentStatus.REVERSED
            ),
        )
    )

    monkeypatch.setattr(
        service,
        "reverse_document_for_trade_fulfillment",
        reverse,
    )

    reserve = AsyncMock()
    consume = AsyncMock()
    outstanding = AsyncMock()

    monkeypatch.setattr(
        service,
        "reserve_source_line",
        reserve,
    )

    monkeypatch.setattr(
        service,
        "consume_source_line_reservation",
        consume,
    )

    monkeypatch.setattr(
        service,
        "get_outstanding_reservation_quantities",
        outstanding,
    )

    result = (
        await execute_purchase_order_fulfillment_reversal(
            db,
            company_id=1,
            trade_document_id=order.id,
            fulfillment_id=fulfillment.id,
            reversal_date=date(
                2026,
                8,
                28,
            ),
            reversed_by=99,
        )
    )

    assert result.purchase_order is order
    assert result.fulfillment is fulfillment

    assert (
        result.warehouse_document.status
        == DocumentStatus.REVERSED
    )

    assert (
        order.status
        == TradeDocumentStatus.CONFIRMED
    )

    assert fulfilled.await_count == 2

    for call in fulfilled.await_args_list:
        assert (
            call.kwargs[
                "warehouse_document_type"
            ]
            == DocumentType.RECEIPT
        )

    reverse.assert_awaited_once_with(
        db,
        company_id=1,
        document_id=30,
        fulfillment_id=20,
        reversal_date=date(
            2026,
            8,
            28,
        ),
        reversed_by=99,
    )

    reserve.assert_not_awaited()
    consume.assert_not_awaited()
    outstanding.assert_not_awaited()

    db.flush.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_purchase_reversal_fulfilled_to_partial(
    monkeypatch,
):
    order, line = make_order(
        status=TradeDocumentStatus.FULFILLED
    )

    fulfillment = make_fulfillment()

    mapping = make_mapping(
        line=line,
        quantity="3.0000",
    )

    db = SimpleNamespace(
        flush=AsyncMock()
    )

    monkeypatch.setattr(
        service,
        "get_locked_purchase_order",
        AsyncMock(
            return_value=order
        ),
    )

    monkeypatch.setattr(
        service,
        "get_locked_purchase_trade_fulfillment",
        AsyncMock(
            return_value=fulfillment
        ),
    )

    monkeypatch.setattr(
        service,
        "get_trade_fulfillment_lines_for_reversal",
        AsyncMock(
            return_value=(
                mapping,
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "get_persisted_fulfilled_quantities",
        AsyncMock(
            side_effect=[
                {
                    line.id: Decimal("5.0000"),
                },
                {
                    line.id: Decimal("2.0000"),
                },
            ]
        ),
    )

    monkeypatch.setattr(
        service,
        "reverse_document_for_trade_fulfillment",
        AsyncMock(
            return_value=SimpleNamespace(
                id=30,
                document_type=(
                    DocumentType.RECEIPT
                ),
                status=(
                    DocumentStatus.REVERSED
                ),
            )
        ),
    )

    await execute_purchase_order_fulfillment_reversal(
        db,
        company_id=1,
        trade_document_id=order.id,
        fulfillment_id=20,
        reversal_date=date(
            2026,
            8,
            28,
        ),
        reversed_by=99,
    )

    assert (
        order.status
        == (
            TradeDocumentStatus
            .PARTIALLY_FULFILLED
        )
    )


@pytest.mark.asyncio
async def test_purchase_reversal_rejects_issue_fulfillment(
    monkeypatch,
):
    order, line = make_order()

    fulfillment = make_fulfillment()

    fulfillment.warehouse_document_type = (
        DocumentType.ISSUE.value
    )

    db = SimpleNamespace(
        flush=AsyncMock()
    )

    monkeypatch.setattr(
        service,
        "get_locked_purchase_order",
        AsyncMock(
            return_value=order
        ),
    )

    monkeypatch.setattr(
        service,
        "get_locked_purchase_trade_fulfillment",
        AsyncMock(
            return_value=fulfillment
        ),
    )

    reverse = AsyncMock()

    monkeypatch.setattr(
        service,
        "reverse_document_for_trade_fulfillment",
        reverse,
    )

    with pytest.raises(
        PurchaseOrderFulfillmentReversalStateError,
        match="RECEIPT",
    ):
        await execute_purchase_order_fulfillment_reversal(
            db,
            company_id=1,
            trade_document_id=order.id,
            fulfillment_id=20,
            reversal_date=date(
                2026,
                8,
                28,
            ),
            reversed_by=99,
        )

    reverse.assert_not_awaited()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_purchase_reversal_rejects_inconsistent_lifecycle_state(
    monkeypatch,
):
    order, line = make_order(
        status=TradeDocumentStatus.FULFILLED
    )

    fulfillment = make_fulfillment()

    mapping = make_mapping(
        line=line,
        quantity="2.0000",
    )

    db = SimpleNamespace(
        flush=AsyncMock()
    )

    monkeypatch.setattr(
        service,
        "get_locked_purchase_order",
        AsyncMock(
            return_value=order
        ),
    )

    monkeypatch.setattr(
        service,
        "get_locked_purchase_trade_fulfillment",
        AsyncMock(
            return_value=fulfillment
        ),
    )

    monkeypatch.setattr(
        service,
        "get_trade_fulfillment_lines_for_reversal",
        AsyncMock(
            return_value=(
                mapping,
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "get_persisted_fulfilled_quantities",
        AsyncMock(
            return_value={
                line.id: Decimal("2.0000"),
            }
        ),
    )

    reverse = AsyncMock()

    monkeypatch.setattr(
        service,
        "reverse_document_for_trade_fulfillment",
        reverse,
    )

    with pytest.raises(
        PurchaseOrderFulfillmentReversalStateError,
        match="lifecycle status",
    ):
        await execute_purchase_order_fulfillment_reversal(
            db,
            company_id=1,
            trade_document_id=order.id,
            fulfillment_id=20,
            reversal_date=date(
                2026,
                8,
                28,
            ),
            reversed_by=99,
        )

    reverse.assert_not_awaited()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_purchase_reversal_requires_exact_fulfillment_delta(
    monkeypatch,
):
    order, line = make_order(
        status=(
            TradeDocumentStatus
            .PARTIALLY_FULFILLED
        )
    )

    fulfillment = make_fulfillment()

    mapping = make_mapping(
        line=line,
        quantity="2.0000",
    )

    db = SimpleNamespace(
        flush=AsyncMock()
    )

    monkeypatch.setattr(
        service,
        "get_locked_purchase_order",
        AsyncMock(
            return_value=order
        ),
    )

    monkeypatch.setattr(
        service,
        "get_locked_purchase_trade_fulfillment",
        AsyncMock(
            return_value=fulfillment
        ),
    )

    monkeypatch.setattr(
        service,
        "get_trade_fulfillment_lines_for_reversal",
        AsyncMock(
            return_value=(
                mapping,
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "get_persisted_fulfilled_quantities",
        AsyncMock(
            side_effect=[
                {
                    line.id: Decimal("2.0000"),
                },
                {
                    line.id: Decimal("1.0000"),
                },
            ]
        ),
    )

    monkeypatch.setattr(
        service,
        "reverse_document_for_trade_fulfillment",
        AsyncMock(
            return_value=SimpleNamespace(
                id=30,
                document_type=(
                    DocumentType.RECEIPT
                ),
                status=(
                    DocumentStatus.REVERSED
                ),
            )
        ),
    )

    with pytest.raises(
        PurchaseOrderFulfillmentReversalStateError,
        match="exact reversed",
    ):
        await execute_purchase_order_fulfillment_reversal(
            db,
            company_id=1,
            trade_document_id=order.id,
            fulfillment_id=20,
            reversal_date=date(
                2026,
                8,
                28,
            ),
            reversed_by=99,
        )

    db.flush.assert_not_awaited()
