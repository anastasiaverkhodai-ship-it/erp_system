from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.trade_document_lifecycle_service as lifecycle
from app.services.trade_document_lifecycle_service import (
    PurchaseOrderLinesRequiredError,
    PurchaseOrderStatusError,
    PurchaseOrderTypeError,
    PurchaseOrderWarehouseRequiredError,
    confirm_purchase_order,
    validate_purchase_order_confirmation,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


def make_line(
    *,
    line_number: int = 1,
    product_id: int = 10,
    warehouse_id: int | None = 20,
):
    return SimpleNamespace(
        id=line_number,
        line_number=line_number,
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity=5,
    )


def make_order(
    *,
    direction=TradeDirection.PURCHASE,
    kind=TradeDocumentKind.ORDER,
    status=TradeDocumentStatus.DRAFT,
    lines=None,
):
    if lines is None:
        lines = [
            make_line()
        ]

    return SimpleNamespace(
        id=100,
        company_id=1,
        counterparty_id=2,
        contract_id=None,
        number="PO-TEST",
        direction=direction,
        kind=kind,
        status=status,
        document_date=date(2026, 8, 28),
        currency_code="UAH",
        confirmed_at=None,
        lines=lines,
    )


def test_purchase_order_confirmation_accepts_draft_order():
    document = make_order()

    validate_purchase_order_confirmation(
        document
    )


def test_purchase_order_confirmation_rejects_sale_direction():
    document = make_order(
        direction=TradeDirection.SALE
    )

    with pytest.raises(
        PurchaseOrderTypeError
    ):
        validate_purchase_order_confirmation(
            document
        )


def test_purchase_order_confirmation_rejects_invoice():
    document = make_order(
        kind=TradeDocumentKind.INVOICE
    )

    with pytest.raises(
        PurchaseOrderTypeError
    ):
        validate_purchase_order_confirmation(
            document
        )


def test_purchase_order_confirmation_requires_draft():
    document = make_order(
        status=TradeDocumentStatus.CONFIRMED
    )

    with pytest.raises(
        PurchaseOrderStatusError
    ):
        validate_purchase_order_confirmation(
            document
        )


def test_purchase_order_confirmation_requires_lines():
    document = make_order(
        lines=[]
    )

    with pytest.raises(
        PurchaseOrderLinesRequiredError
    ):
        validate_purchase_order_confirmation(
            document
        )


def test_purchase_order_confirmation_requires_warehouse():
    document = make_order(
        lines=[
            make_line(
                warehouse_id=None
            )
        ]
    )

    with pytest.raises(
        PurchaseOrderWarehouseRequiredError
    ):
        validate_purchase_order_confirmation(
            document
        )


@pytest.mark.asyncio
async def test_confirm_purchase_order_sets_confirmed_without_reservation(
    monkeypatch,
):
    document = make_order()

    db = SimpleNamespace(
        flush=AsyncMock()
    )

    get_locked = AsyncMock(
        return_value=document
    )

    revalidate = AsyncMock()

    reserve = AsyncMock()

    monkeypatch.setattr(
        lifecycle,
        "get_locked_purchase_order",
        get_locked,
    )

    monkeypatch.setattr(
        lifecycle,
        "revalidate_purchase_order_references",
        revalidate,
    )

    monkeypatch.setattr(
        lifecycle,
        "reserve_source_line",
        reserve,
    )

    result = await confirm_purchase_order(
        db,
        company_id=1,
        document_id=100,
    )

    assert result is document

    assert (
        document.status
        == TradeDocumentStatus.CONFIRMED
    )

    assert document.confirmed_at is not None

    get_locked.assert_awaited_once_with(
        db,
        company_id=1,
        document_id=100,
    )

    revalidate.assert_awaited_once_with(
        db,
        document=document,
    )

    reserve.assert_not_awaited()

    db.flush.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_confirm_purchase_order_revalidates_before_mutation(
    monkeypatch,
):
    document = make_order()

    db = SimpleNamespace(
        flush=AsyncMock()
    )

    get_locked = AsyncMock(
        return_value=document
    )

    failure = lifecycle.PurchaseOrderProductInvalidError(
        "Product is inactive"
    )

    revalidate = AsyncMock(
        side_effect=failure
    )

    monkeypatch.setattr(
        lifecycle,
        "get_locked_purchase_order",
        get_locked,
    )

    monkeypatch.setattr(
        lifecycle,
        "revalidate_purchase_order_references",
        revalidate,
    )

    with pytest.raises(
        lifecycle.PurchaseOrderProductInvalidError
    ):
        await confirm_purchase_order(
            db,
            company_id=1,
            document_id=100,
        )

    assert (
        document.status
        == TradeDocumentStatus.DRAFT
    )

    assert document.confirmed_at is None

    db.flush.assert_not_awaited()
