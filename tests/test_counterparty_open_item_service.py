from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.counterparty_open_item_service as service
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


def make_line(
    *,
    quantity="2.0000",
    unit_price="125.5000",
):
    return SimpleNamespace(
        quantity=Decimal(quantity),
        unit_price=Decimal(unit_price),
    )


def make_invoice(
    *,
    direction=TradeDirection.SALE,
    status=TradeDocumentStatus.CONFIRMED,
    payment_term_days=14,
):
    return SimpleNamespace(
        id=100,
        company_id=1,
        counterparty_id=20,
        contract_id=None,
        direction=direction,
        kind=TradeDocumentKind.INVOICE,
        status=status,
        document_date=date(
            2026,
            8,
            28,
        ),
        payment_term_days=payment_term_days,
        currency_code="UAH",
        lines=[
            make_line()
        ],
    )


def test_sales_invoice_maps_to_receivable():
    invoice = make_invoice(
        direction=TradeDirection.SALE
    )

    assert (
        service.get_open_item_type_for_invoice(
            invoice
        )
        == CounterpartyOpenItemType.RECEIVABLE
    )


def test_purchase_invoice_maps_to_payable():
    invoice = make_invoice(
        direction=TradeDirection.PURCHASE
    )

    assert (
        service.get_open_item_type_for_invoice(
            invoice
        )
        == CounterpartyOpenItemType.PAYABLE
    )


def test_due_date_uses_payment_terms():
    invoice = make_invoice(
        payment_term_days=14
    )

    assert (
        service.calculate_invoice_due_date(
            invoice
        )
        == date(
            2026,
            9,
            11,
        )
    )


def test_zero_payment_terms_due_same_day():
    invoice = make_invoice(
        payment_term_days=0
    )

    assert (
        service.calculate_invoice_due_date(
            invoice
        )
        == invoice.document_date
    )


def test_invoice_amount_is_currency_rounded():
    invoice = make_invoice()

    assert (
        service.calculate_invoice_open_item_amount(
            invoice
        )
        == Decimal("251.00")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "direction",
        "expected_type",
    ),
    [
        (
            TradeDirection.SALE,
            CounterpartyOpenItemType.RECEIVABLE,
        ),
        (
            TradeDirection.PURCHASE,
            CounterpartyOpenItemType.PAYABLE,
        ),
    ],
)
async def test_create_open_item(
    direction,
    expected_type,
):
    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    invoice = make_invoice(
        direction=direction
    )

    item = (
        await service.create_counterparty_open_item_for_invoice(
            db,
            document=invoice,
        )
    )

    db.add.assert_called_once_with(
        item
    )

    db.flush.assert_awaited_once_with()

    assert item.company_id == 1
    assert item.trade_document_id == 100
    assert item.counterparty_id == 20
    assert item.contract_id is None

    assert (
        item.item_type
        == expected_type
    )

    assert (
        item.status
        == CounterpartyOpenItemStatus.OPEN
    )

    assert (
        item.document_date
        == date(
            2026,
            8,
            28,
        )
    )

    assert (
        item.due_date
        == date(
            2026,
            9,
            11,
        )
    )

    assert item.currency_code == "UAH"

    assert (
        item.original_amount
        == Decimal("251.00")
    )


@pytest.mark.asyncio
async def test_create_open_item_requires_confirmed_invoice():
    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    invoice = make_invoice(
        status=TradeDocumentStatus.DRAFT
    )

    with pytest.raises(
        service.CounterpartyOpenItemSourceStatusError
    ):
        await service.create_counterparty_open_item_for_invoice(
            db,
            document=invoice,
        )

    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_open_item():
    db = SimpleNamespace(
        flush=AsyncMock(),
    )

    invoice = make_invoice()

    item = SimpleNamespace(
        item_type=(
            CounterpartyOpenItemType.RECEIVABLE
        ),
        status=(
            CounterpartyOpenItemStatus.OPEN
        ),
        counterparty_id=20,
        contract_id=None,
        currency_code="UAH",
    )

    original = (
        service.get_locked_open_item_for_invoice
    )

    try:
        service.get_locked_open_item_for_invoice = (
            AsyncMock(
                return_value=item
            )
        )

        result = (
            await service.cancel_counterparty_open_item_for_invoice(
                db,
                document=invoice,
            )
        )
    finally:
        service.get_locked_open_item_for_invoice = (
            original
        )

    assert result is item

    assert (
        item.status
        == CounterpartyOpenItemStatus.CANCELLED
    )

    db.flush.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cancel_rejects_partially_settled_item():
    db = SimpleNamespace(
        flush=AsyncMock(),
    )

    invoice = make_invoice()

    item = SimpleNamespace(
        item_type=(
            CounterpartyOpenItemType.RECEIVABLE
        ),
        status=(
            CounterpartyOpenItemStatus.PARTIALLY_SETTLED
        ),
        counterparty_id=20,
        contract_id=None,
        currency_code="UAH",
    )

    original = (
        service.get_locked_open_item_for_invoice
    )

    try:
        service.get_locked_open_item_for_invoice = (
            AsyncMock(
                return_value=item
            )
        )

        with pytest.raises(
            service.CounterpartyOpenItemStateError
        ):
            await service.cancel_counterparty_open_item_for_invoice(
                db,
                document=invoice,
            )
    finally:
        service.get_locked_open_item_for_invoice = (
            original
        )

    db.flush.assert_not_awaited()
