from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.document import (
    DocumentStatus,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)
from app.services import (
    trade_fulfillment_service as service,
)


class FakeDB:
    def __init__(self):
        self.flush_count = 0

    async def flush(self):
        self.flush_count += 1


def make_line(
    *,
    line_id: int,
    product_id: int = 1,
    warehouse_id: int = 1,
    quantity: str = "5.0000",
):
    return SimpleNamespace(
        id=line_id,
        line_number=1,
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity=Decimal(quantity),
    )


def make_order(
    *,
    status=(
        TradeDocumentStatus.PARTIALLY_FULFILLED
    ),
    quantity: str = "5.0000",
):
    line = make_line(
        line_id=101,
        quantity=quantity,
    )

    order = SimpleNamespace(
        id=10,
        company_id=1,
        direction=TradeDirection.SALE,
        kind=TradeDocumentKind.ORDER,
        status=status,
        lines=[line],
    )

    return order, line


def test_reversal_state_accepts_partial_and_fulfilled():
    for status in (
        TradeDocumentStatus.PARTIALLY_FULFILLED,
        TradeDocumentStatus.FULFILLED,
    ):
        order, _ = make_order(
            status=status
        )

        service.validate_sales_order_fulfillment_reversal_state(
            order
        )


def test_reversal_state_rejects_confirmed():
    order, _ = make_order(
        status=TradeDocumentStatus.CONFIRMED
    )

    with pytest.raises(
        service.SalesOrderFulfillmentReversalStatusError
    ):
        service.validate_sales_order_fulfillment_reversal_state(
            order
        )


def test_reversal_balance_invariant():
    order, line = make_order()

    service.validate_sales_order_reversal_balances(
        order,
        fulfilled_quantities={
            line.id: Decimal("2.0000"),
        },
        reserved_quantities={
            line.id: Decimal("3.0000"),
        },
    )

    with pytest.raises(
        service.SalesOrderFulfillmentReversalStateError
    ):
        service.validate_sales_order_reversal_balances(
            order,
            fulfilled_quantities={
                line.id: Decimal("2.0000"),
            },
            reserved_quantities={
                line.id: Decimal("2.0000"),
            },
        )


@pytest.mark.asyncio
async def test_atomic_partial_fulfillment_reversal(
    monkeypatch,
):
    db = FakeDB()

    order, line = make_order(
        status=(
            TradeDocumentStatus.PARTIALLY_FULFILLED
        )
    )

    fulfillment = SimpleNamespace(
        id=20,
        company_id=1,
        trade_document_id=order.id,
        warehouse_document_id=30,
    )

    mapping = SimpleNamespace(
        id=40,
        company_id=1,
        fulfillment_id=fulfillment.id,
        trade_document_id=order.id,
        trade_document_line_id=line.id,
        warehouse_document_id=(
            fulfillment.warehouse_document_id
        ),
        product_id=line.product_id,
        warehouse_id=line.warehouse_id,
        quantity=Decimal("2.0000"),
    )

    reversed_document = SimpleNamespace(
        id=fulfillment.warehouse_document_id,
        status=DocumentStatus.REVERSED,
    )

    events = []

    async def fake_get_locked_trade_document(
        db,
        *,
        company_id,
        document_id,
    ):
        events.append("lock_order")
        return order

    async def fake_get_locked_fulfillment(
        db,
        *,
        company_id,
        trade_document_id,
        fulfillment_id,
    ):
        events.append("lock_fulfillment")
        return fulfillment

    async def fake_get_mappings(
        db,
        *,
        company_id,
        trade_document_id,
        fulfillment_id,
    ):
        events.append("lock_mappings")
        return (mapping,)

    async def fake_lock_source(
        db,
        *,
        company_id,
        source_document_id,
        source_document_line_id,
    ):
        events.append("lock_source")
        return line

    fulfilled_values = iter(
        [
            {
                line.id: Decimal("2.0000"),
            },
            {
                line.id: Decimal("0.0000"),
            },
        ]
    )

    reserved_values = iter(
        [
            {
                line.id: Decimal("3.0000"),
            },
            {
                line.id: Decimal("5.0000"),
            },
        ]
    )

    async def fake_fulfilled(*args, **kwargs):
        return next(
            fulfilled_values
        )

    async def fake_reserved(*args, **kwargs):
        return next(
            reserved_values
        )

    async def fake_reverse(
        db,
        *,
        company_id,
        document_id,
        fulfillment_id,
        reversal_date,
        reversed_by,
    ):
        events.append("reverse_document")

        assert (
            events.index("lock_source")
            < events.index("reverse_document")
        )

        return reversed_document

    async def fake_reserve(
        db,
        *,
        company_id,
        source_document_id,
        source_document_line_id,
        quantity,
    ):
        events.append("reserve")

        assert (
            events.index("reverse_document")
            < events.index("reserve")
        )

        assert quantity == Decimal(
            "2.0000"
        )

        return SimpleNamespace()

    monkeypatch.setattr(
        service,
        "get_locked_trade_document",
        fake_get_locked_trade_document,
    )

    monkeypatch.setattr(
        service,
        "get_locked_trade_fulfillment",
        fake_get_locked_fulfillment,
    )

    monkeypatch.setattr(
        service,
        "get_trade_fulfillment_lines_for_reversal",
        fake_get_mappings,
    )

    monkeypatch.setattr(
        service,
        "get_locked_source_line",
        fake_lock_source,
    )

    monkeypatch.setattr(
        service,
        "get_persisted_fulfilled_quantities",
        fake_fulfilled,
    )

    monkeypatch.setattr(
        service,
        "get_outstanding_reservation_quantities",
        fake_reserved,
    )

    monkeypatch.setattr(
        service,
        "reverse_document_for_trade_fulfillment",
        fake_reverse,
    )

    monkeypatch.setattr(
        service,
        "reserve_source_line",
        fake_reserve,
    )

    result = (
        await service.execute_sales_order_fulfillment_reversal(
            db,
            company_id=1,
            trade_document_id=order.id,
            fulfillment_id=fulfillment.id,
            reversal_date=SimpleNamespace(),
            reversed_by=99,
        )
    )

    assert result.sales_order is order
    assert result.fulfillment is fulfillment
    assert (
        result.warehouse_document
        is reversed_document
    )

    assert (
        order.status
        == TradeDocumentStatus.CONFIRMED
    )

    assert events == [
        "lock_order",
        "lock_fulfillment",
        "lock_mappings",
        "lock_source",
        "reverse_document",
        "reserve",
    ]

    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_reversal_from_fulfilled_returns_partial(
    monkeypatch,
):
    db = FakeDB()

    order, line = make_order(
        status=TradeDocumentStatus.FULFILLED
    )

    fulfillment = SimpleNamespace(
        id=20,
        company_id=1,
        trade_document_id=order.id,
        warehouse_document_id=30,
    )

    mapping = SimpleNamespace(
        id=40,
        company_id=1,
        fulfillment_id=20,
        trade_document_id=order.id,
        trade_document_line_id=line.id,
        warehouse_document_id=30,
        product_id=1,
        warehouse_id=1,
        quantity=Decimal("3.0000"),
    )

    async def return_order(*args, **kwargs):
        return order

    async def return_fulfillment(*args, **kwargs):
        return fulfillment

    async def return_mappings(*args, **kwargs):
        return (mapping,)

    async def return_line(*args, **kwargs):
        return line

    fulfilled_values = iter(
        [
            {
                line.id: Decimal("5.0000"),
            },
            {
                line.id: Decimal("2.0000"),
            },
        ]
    )

    reserved_values = iter(
        [
            {
                line.id: Decimal("0.0000"),
            },
            {
                line.id: Decimal("3.0000"),
            },
        ]
    )

    async def fulfilled(*args, **kwargs):
        return next(
            fulfilled_values
        )

    async def reserved(*args, **kwargs):
        return next(
            reserved_values
        )

    async def reverse(*args, **kwargs):
        return SimpleNamespace(
            id=30,
            status=DocumentStatus.REVERSED,
        )

    async def reserve(*args, **kwargs):
        return SimpleNamespace()

    monkeypatch.setattr(
        service,
        "get_locked_trade_document",
        return_order,
    )
    monkeypatch.setattr(
        service,
        "get_locked_trade_fulfillment",
        return_fulfillment,
    )
    monkeypatch.setattr(
        service,
        "get_trade_fulfillment_lines_for_reversal",
        return_mappings,
    )
    monkeypatch.setattr(
        service,
        "get_locked_source_line",
        return_line,
    )
    monkeypatch.setattr(
        service,
        "get_persisted_fulfilled_quantities",
        fulfilled,
    )
    monkeypatch.setattr(
        service,
        "get_outstanding_reservation_quantities",
        reserved,
    )
    monkeypatch.setattr(
        service,
        "reverse_document_for_trade_fulfillment",
        reverse,
    )
    monkeypatch.setattr(
        service,
        "reserve_source_line",
        reserve,
    )

    await service.execute_sales_order_fulfillment_reversal(
        db,
        company_id=1,
        trade_document_id=order.id,
        fulfillment_id=20,
        reversal_date=SimpleNamespace(),
        reversed_by=99,
    )

    assert (
        order.status
        == TradeDocumentStatus.PARTIALLY_FULFILLED
    )
