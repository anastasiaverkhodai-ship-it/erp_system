from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.trade_document_lifecycle_service as lifecycle
from app.services.trade_document_lifecycle_service import (
    PurchaseOrderStatusError,
    PurchaseOrderTypeError,
    cancel_purchase_order,
    validate_purchase_order_cancellation,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


def make_order(
    *,
    direction=TradeDirection.PURCHASE,
    kind=TradeDocumentKind.ORDER,
    status=TradeDocumentStatus.DRAFT,
):
    return SimpleNamespace(
        id=100,
        company_id=1,
        counterparty_id=2,
        contract_id=None,
        number="PO-CANCEL-TEST",
        direction=direction,
        kind=kind,
        status=status,
        document_date=date(2026, 8, 28),
        currency_code="UAH",
        confirmed_at=None,
        cancelled_at=None,
        lines=[],
    )


@pytest.mark.parametrize(
    "status",
    [
        TradeDocumentStatus.DRAFT,
        TradeDocumentStatus.CONFIRMED,
    ],
)
def test_purchase_order_cancellation_accepts_valid_status(
    status,
):
    document = make_order(
        status=status
    )

    validate_purchase_order_cancellation(
        document
    )


def test_purchase_order_cancellation_rejects_sale():
    document = make_order(
        direction=TradeDirection.SALE
    )

    with pytest.raises(
        PurchaseOrderTypeError
    ):
        validate_purchase_order_cancellation(
            document
        )


def test_purchase_order_cancellation_rejects_invoice():
    document = make_order(
        kind=TradeDocumentKind.INVOICE
    )

    with pytest.raises(
        PurchaseOrderTypeError
    ):
        validate_purchase_order_cancellation(
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
def test_purchase_order_cancellation_rejects_invalid_status(
    status,
):
    document = make_order(
        status=status
    )

    with pytest.raises(
        PurchaseOrderStatusError
    ):
        validate_purchase_order_cancellation(
            document
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_status",
    [
        TradeDocumentStatus.DRAFT,
        TradeDocumentStatus.CONFIRMED,
    ],
)
async def test_cancel_purchase_order_sets_cancelled_without_reservations(
    monkeypatch,
    initial_status,
):
    document = make_order(
        status=initial_status
    )

    db = SimpleNamespace(
        flush=AsyncMock()
    )

    get_locked = AsyncMock(
        return_value=document
    )

    release = AsyncMock()
    get_reserved = AsyncMock()

    monkeypatch.setattr(
        lifecycle,
        "get_locked_purchase_order",
        get_locked,
    )

    monkeypatch.setattr(
        lifecycle,
        "release_source_line",
        release,
    )

    monkeypatch.setattr(
        lifecycle,
        "get_reserved_quantity_for_source_line",
        get_reserved,
    )

    result = await cancel_purchase_order(
        db,
        company_id=1,
        document_id=100,
    )

    assert result is document

    assert (
        document.status
        == TradeDocumentStatus.CANCELLED
    )

    assert document.cancelled_at is not None

    release.assert_not_awaited()
    get_reserved.assert_not_awaited()

    db.flush.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cancel_purchase_order_rejects_repeat_cancel(
    monkeypatch,
):
    document = make_order(
        status=TradeDocumentStatus.CANCELLED
    )

    db = SimpleNamespace(
        flush=AsyncMock()
    )

    monkeypatch.setattr(
        lifecycle,
        "get_locked_purchase_order",
        AsyncMock(
            return_value=document
        ),
    )

    with pytest.raises(
        PurchaseOrderStatusError
    ):
        await cancel_purchase_order(
            db,
            company_id=1,
            document_id=100,
        )

    db.flush.assert_not_awaited()
