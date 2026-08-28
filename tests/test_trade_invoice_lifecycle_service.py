from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.trade_document_lifecycle_service as service
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


class FakeDB:
    def __init__(self):
        self.flush = AsyncMock()


def make_line(
    *,
    quantity="2.0000",
    unit_price="125.5000",
    warehouse_id=None,
):
    return SimpleNamespace(
        quantity=Decimal(
            quantity
        ),
        unit_price=Decimal(
            unit_price
        ),
        warehouse_id=warehouse_id,
        product_id=1,
        line_number=1,
    )


def make_invoice(
    *,
    direction=TradeDirection.SALE,
    kind=TradeDocumentKind.INVOICE,
    status=TradeDocumentStatus.DRAFT,
    lines=None,
):
    if lines is None:
        lines = [
            make_line()
        ]

    return SimpleNamespace(
        id=10,
        company_id=1,
        counterparty_id=20,
        contract_id=None,
        direction=direction,
        kind=kind,
        status=status,
        document_date=None,
        currency_code="UAH",
        lines=lines,
        confirmed_at=None,
        cancelled_at=None,
    )


def test_invoice_total():
    invoice = make_invoice(
        lines=[
            make_line(
                quantity="2",
                unit_price="10.50",
            ),
            make_line(
                quantity="3",
                unit_price="4.00",
            ),
        ]
    )

    assert (
        service.calculate_trade_invoice_total(
            invoice
        )
        == Decimal("33.00")
    )


@pytest.mark.parametrize(
    "direction",
    [
        TradeDirection.SALE,
        TradeDirection.PURCHASE,
    ],
)
def test_invoice_confirmation_accepts_optional_warehouse(
    direction,
):
    invoice = make_invoice(
        direction=direction,
        lines=[
            make_line(
                warehouse_id=None
            )
        ],
    )

    service.validate_trade_invoice_confirmation(
        invoice,
        expected_direction=direction,
    )


def test_invoice_confirmation_rejects_order():
    invoice = make_invoice(
        kind=TradeDocumentKind.ORDER
    )

    with pytest.raises(
        service.TradeInvoiceTypeError
    ):
        service.validate_trade_invoice_confirmation(
            invoice,
            expected_direction=TradeDirection.SALE,
        )


def test_invoice_confirmation_rejects_wrong_direction():
    invoice = make_invoice(
        direction=TradeDirection.PURCHASE
    )

    with pytest.raises(
        service.TradeInvoiceTypeError
    ):
        service.validate_trade_invoice_confirmation(
            invoice,
            expected_direction=TradeDirection.SALE,
        )


@pytest.mark.parametrize(
    "status",
    [
        TradeDocumentStatus.CONFIRMED,
        TradeDocumentStatus.PARTIALLY_FULFILLED,
        TradeDocumentStatus.FULFILLED,
        TradeDocumentStatus.CANCELLED,
    ],
)
def test_invoice_confirmation_rejects_non_draft(
    status,
):
    invoice = make_invoice(
        status=status
    )

    with pytest.raises(
        service.TradeInvoiceStatusError
    ):
        service.validate_trade_invoice_confirmation(
            invoice,
            expected_direction=TradeDirection.SALE,
        )


def test_invoice_confirmation_requires_lines():
    invoice = make_invoice(
        lines=[]
    )

    with pytest.raises(
        service.TradeInvoiceLinesRequiredError
    ):
        service.validate_trade_invoice_confirmation(
            invoice,
            expected_direction=TradeDirection.SALE,
        )


def test_invoice_confirmation_requires_positive_total():
    invoice = make_invoice(
        lines=[
            make_line(
                unit_price="0"
            )
        ]
    )

    with pytest.raises(
        service.TradeInvoiceAmountError
    ):
        service.validate_trade_invoice_confirmation(
            invoice,
            expected_direction=TradeDirection.SALE,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "direction",
        "function_name",
    ),
    [
        (
            TradeDirection.SALE,
            "confirm_sales_invoice",
        ),
        (
            TradeDirection.PURCHASE,
            "confirm_purchase_invoice",
        ),
    ],
)
async def test_confirm_invoice(
    monkeypatch,
    direction,
    function_name,
):
    db = FakeDB()

    invoice = make_invoice(
        direction=direction,
        lines=[
            make_line(
                warehouse_id=None
            )
        ],
    )

    locked = AsyncMock(
        return_value=invoice
    )

    revalidate = AsyncMock()
    create_open_item = AsyncMock()

    monkeypatch.setattr(
        service,
        "get_locked_trade_invoice",
        locked,
    )

    monkeypatch.setattr(
        service,
        "revalidate_trade_invoice_references",
        revalidate,
    )

    monkeypatch.setattr(
        service,
        "create_counterparty_open_item_for_invoice",
        create_open_item,
    )

    result = await getattr(
        service,
        function_name,
    )(
        db,
        company_id=1,
        document_id=10,
    )

    assert result is invoice

    assert (
        invoice.status
        == TradeDocumentStatus.CONFIRMED
    )

    assert (
        invoice.confirmed_at
        is not None
    )

    locked.assert_awaited_once_with(
        db,
        company_id=1,
        document_id=10,
    )

    revalidate.assert_awaited_once_with(
        db,
        document=invoice,
    )

    create_open_item.assert_awaited_once_with(
        db,
        document=invoice,
    )

    db.flush.assert_awaited_once_with()


@pytest.mark.parametrize(
    "status",
    [
        TradeDocumentStatus.DRAFT,
        TradeDocumentStatus.CONFIRMED,
    ],
)
def test_invoice_cancellation_accepts_draft_and_confirmed(
    status,
):
    invoice = make_invoice(
        status=status
    )

    service.validate_trade_invoice_cancellation(
        invoice,
        expected_direction=TradeDirection.SALE,
    )


@pytest.mark.parametrize(
    "status",
    [
        TradeDocumentStatus.PARTIALLY_FULFILLED,
        TradeDocumentStatus.FULFILLED,
        TradeDocumentStatus.CANCELLED,
    ],
)
def test_invoice_cancellation_rejects_invalid_status(
    status,
):
    invoice = make_invoice(
        status=status
    )

    with pytest.raises(
        service.TradeInvoiceStatusError
    ):
        service.validate_trade_invoice_cancellation(
            invoice,
            expected_direction=TradeDirection.SALE,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "direction",
        "function_name",
    ),
    [
        (
            TradeDirection.SALE,
            "cancel_sales_invoice",
        ),
        (
            TradeDirection.PURCHASE,
            "cancel_purchase_invoice",
        ),
    ],
)
async def test_cancel_invoice(
    monkeypatch,
    direction,
    function_name,
):
    db = FakeDB()

    invoice = make_invoice(
        direction=direction,
        status=(
            TradeDocumentStatus.CONFIRMED
        ),
    )

    locked = AsyncMock(
        return_value=invoice
    )

    cancel_open_item = AsyncMock()

    monkeypatch.setattr(
        service,
        "get_locked_trade_invoice",
        locked,
    )

    monkeypatch.setattr(
        service,
        "cancel_counterparty_open_item_for_invoice",
        cancel_open_item,
    )

    result = await getattr(
        service,
        function_name,
    )(
        db,
        company_id=1,
        document_id=10,
    )

    assert result is invoice

    assert (
        invoice.status
        == TradeDocumentStatus.CANCELLED
    )

    assert (
        invoice.cancelled_at
        is not None
    )

    cancel_open_item.assert_awaited_once_with(
        db,
        document=invoice,
    )

    db.flush.assert_awaited_once_with()


def test_invoice_lifecycle_has_no_financial_or_stock_side_effects():
    from pathlib import Path

    text = Path(
        "app/services/"
        "trade_document_lifecycle_service.py"
    ).read_text()

    start = text.index(
        "# TRADE INVOICE LIFECYCLE"
    )

    block = text[start:]

    forbidden = (
        "reserve_source_line(",
        "release_source_line(",
        "consume_source_line_reservation(",
        "post_document(",
        "JournalEntry(",
        "StockLedger(",
        "StockBalance(",
        "InventoryCostEntry(",
    )

    for token in forbidden:
        assert token not in block, token
