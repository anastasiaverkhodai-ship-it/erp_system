import asyncio
from datetime import date
from decimal import Decimal

import pytest

import app.services.trade_document_lifecycle_service as lifecycle
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.services.trade_document_lifecycle_service import (
    SalesOrderReservationStateError,
    SalesOrderStatusError,
    SalesOrderTypeError,
    cancel_sales_order,
    cancellation_release_order,
    validate_sales_order_cancellation,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


def _document(
    *,
    direction: TradeDirection = TradeDirection.SALE,
    kind: TradeDocumentKind = TradeDocumentKind.ORDER,
    status: TradeDocumentStatus = TradeDocumentStatus.DRAFT,
) -> TradeDocument:
    document = TradeDocument(
        id=100,
        company_id=1,
        counterparty_id=10,
        contract_id=None,
        number="SO-CANCEL-TEST",
        direction=direction,
        kind=kind,
        status=status,
        document_date=date(
            2026,
            8,
            25,
        ),
        currency_code="UAH",
        payment_term_days=0,
        created_by=1,
    )

    document.lines = []

    return document


def _line(
    *,
    line_id: int,
    line_number: int,
    product_id: int,
    warehouse_id: int,
) -> TradeDocumentLine:
    return TradeDocumentLine(
        id=line_id,
        company_id=1,
        trade_document_id=100,
        line_number=line_number,
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity=Decimal("5.0000"),
        unit_price=Decimal("100.0000"),
    )


def test_draft_sales_order_can_be_cancelled() -> None:
    document = _document(
        status=TradeDocumentStatus.DRAFT
    )

    validate_sales_order_cancellation(
        document
    )


def test_confirmed_sales_order_can_be_cancelled() -> None:
    document = _document(
        status=TradeDocumentStatus.CONFIRMED
    )

    document.lines = [
        _line(
            line_id=1,
            line_number=1,
            product_id=10,
            warehouse_id=20,
        )
    ]

    validate_sales_order_cancellation(
        document
    )


def test_purchase_order_cannot_use_sales_cancel() -> None:
    document = _document(
        direction=TradeDirection.PURCHASE
    )

    with pytest.raises(
        SalesOrderTypeError
    ):
        validate_sales_order_cancellation(
            document
        )


def test_sales_invoice_cannot_use_order_cancel() -> None:
    document = _document(
        kind=TradeDocumentKind.INVOICE
    )

    with pytest.raises(
        SalesOrderTypeError
    ):
        validate_sales_order_cancellation(
            document
        )


@pytest.mark.parametrize(
    "status",
    [
        TradeDocumentStatus.PARTIALLY_FULFILLED,
        TradeDocumentStatus.FULFILLED,
        TradeDocumentStatus.CANCELLED,
    ],
)
def test_invalid_status_cannot_be_cancelled(
    status: TradeDocumentStatus,
) -> None:
    document = _document(
        status=status
    )

    with pytest.raises(
        SalesOrderStatusError
    ):
        validate_sales_order_cancellation(
            document
        )


def test_draft_has_no_release_order() -> None:
    document = _document(
        status=TradeDocumentStatus.DRAFT
    )

    document.lines = [
        _line(
            line_id=1,
            line_number=1,
            product_id=10,
            warehouse_id=20,
        )
    ]

    assert (
        cancellation_release_order(
            document
        )
        == ()
    )


def test_confirmed_release_order_is_deterministic() -> None:
    document = _document(
        status=TradeDocumentStatus.CONFIRMED
    )

    document.lines = [
        _line(
            line_id=30,
            line_number=1,
            product_id=20,
            warehouse_id=2,
        ),
        _line(
            line_id=20,
            line_number=2,
            product_id=30,
            warehouse_id=1,
        ),
        _line(
            line_id=10,
            line_number=3,
            product_id=10,
            warehouse_id=1,
        ),
        _line(
            line_id=5,
            line_number=4,
            product_id=10,
            warehouse_id=1,
        ),
    ]

    ordered = cancellation_release_order(
        document
    )

    assert [
        line.id
        for line in ordered
    ] == [
        5,
        10,
        20,
        30,
    ]


def test_draft_cancel_creates_no_release(
    monkeypatch,
) -> None:
    document = _document(
        status=TradeDocumentStatus.DRAFT
    )

    release_calls = []

    async def fake_get_locked_trade_document(
        _db,
        *,
        company_id,
        document_id,
    ):
        assert company_id == 1
        assert document_id == 100
        return document

    async def fake_release_source_line(
        *_args,
        **kwargs,
    ):
        release_calls.append(
            kwargs
        )

    class FakeDB:
        async def flush(self):
            return None

    monkeypatch.setattr(
        lifecycle,
        "get_locked_trade_document",
        fake_get_locked_trade_document,
    )

    monkeypatch.setattr(
        lifecycle,
        "release_source_line",
        fake_release_source_line,
    )

    result = asyncio.run(
        cancel_sales_order(
            FakeDB(),
            company_id=1,
            document_id=100,
        )
    )

    assert (
        result.status
        == TradeDocumentStatus.CANCELLED
    )

    assert result.cancelled_at is not None
    assert release_calls == []


def test_confirmed_cancel_releases_outstanding(
    monkeypatch,
) -> None:
    document = _document(
        status=TradeDocumentStatus.CONFIRMED
    )

    line_1 = _line(
        line_id=1,
        line_number=1,
        product_id=10,
        warehouse_id=20,
    )

    line_2 = _line(
        line_id=2,
        line_number=2,
        product_id=11,
        warehouse_id=20,
    )

    document.lines = [
        line_1,
        line_2,
    ]

    release_calls = []

    async def fake_get_locked_trade_document(
        _db,
        *,
        company_id,
        document_id,
    ):
        return document

    async def fake_reserved_quantity(
        _db,
        *,
        source_document_line_id,
        **_kwargs,
    ):
        return {
            1: Decimal("3.0000"),
            2: Decimal("0.0000"),
        }[source_document_line_id]

    async def fake_release_source_line(
        _db,
        **kwargs,
    ):
        release_calls.append(
            kwargs
        )

    class FakeDB:
        async def flush(self):
            return None

    monkeypatch.setattr(
        lifecycle,
        "get_locked_trade_document",
        fake_get_locked_trade_document,
    )

    monkeypatch.setattr(
        lifecycle,
        "get_reserved_quantity_for_source_line",
        fake_reserved_quantity,
    )

    monkeypatch.setattr(
        lifecycle,
        "release_source_line",
        fake_release_source_line,
    )

    result = asyncio.run(
        cancel_sales_order(
            FakeDB(),
            company_id=1,
            document_id=100,
        )
    )

    assert (
        result.status
        == TradeDocumentStatus.CANCELLED
    )

    assert result.cancelled_at is not None

    assert len(release_calls) == 1

    assert (
        release_calls[0]["source_document_line_id"]
        == 1
    )

    assert (
        release_calls[0]["quantity"]
        == Decimal("3.0000")
    )


def test_negative_reservation_balance_rejected(
    monkeypatch,
) -> None:
    document = _document(
        status=TradeDocumentStatus.CONFIRMED
    )

    document.lines = [
        _line(
            line_id=1,
            line_number=1,
            product_id=10,
            warehouse_id=20,
        )
    ]

    async def fake_get_locked_trade_document(
        _db,
        **_kwargs,
    ):
        return document

    async def fake_reserved_quantity(
        _db,
        **_kwargs,
    ):
        return Decimal("-1.0000")

    class FakeDB:
        async def flush(self):
            return None

    monkeypatch.setattr(
        lifecycle,
        "get_locked_trade_document",
        fake_get_locked_trade_document,
    )

    monkeypatch.setattr(
        lifecycle,
        "get_reserved_quantity_for_source_line",
        fake_reserved_quantity,
    )

    with pytest.raises(
        SalesOrderReservationStateError
    ):
        asyncio.run(
            cancel_sales_order(
                FakeDB(),
                company_id=1,
                document_id=100,
            )
        )
