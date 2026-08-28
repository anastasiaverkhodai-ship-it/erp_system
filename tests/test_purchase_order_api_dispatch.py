from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.api.v1.trade_documents as api
from app.models.document import DocumentType
from app.services.trade_document_types import (
    TradeDirection,
)
from app.services.trade_fulfillment_service import (
    PurchaseOrderFulfillmentRequestLine,
    SalesOrderFulfillmentRequestLine,
)


class FakeDB:
    def __init__(self):
        self.commit = AsyncMock()
        self.rollback = AsyncMock()


def fulfillment_data():
    return SimpleNamespace(
        warehouse_document_number="API-FULFILL-1",
        document_date=date(
            2026,
            8,
            28,
        ),
        accounting_rule_id=1,
        lines=[
            SimpleNamespace(
                trade_document_line_id=11,
                quantity=Decimal("2.0000"),
            )
        ],
    )


@pytest.mark.asyncio
async def test_confirm_dispatches_purchase_order(
    monkeypatch,
):
    db = FakeDB()

    purchase = AsyncMock(
        return_value=SimpleNamespace(
            id=50
        )
    )

    sales = AsyncMock()

    reloaded = object()

    monkeypatch.setattr(
        api,
        "_get_trade_document_direction",
        AsyncMock(
            return_value=TradeDirection.PURCHASE
        ),
    )

    monkeypatch.setattr(
        api,
        "confirm_purchase_order",
        purchase,
    )

    monkeypatch.setattr(
        api,
        "confirm_sales_order",
        sales,
    )

    monkeypatch.setattr(
        api,
        "_load_trade_document",
        AsyncMock(
            return_value=reloaded
        ),
    )

    result = (
        await api.confirm_trade_document_sales_order(
            company_id=1,
            document_id=50,
            db=db,
            _permission=None,
        )
    )

    assert result is reloaded

    purchase.assert_awaited_once_with(
        db,
        company_id=1,
        document_id=50,
    )

    sales.assert_not_awaited()
    db.commit.assert_awaited_once_with()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_preserves_sales_dispatch(
    monkeypatch,
):
    db = FakeDB()

    purchase = AsyncMock()

    sales = AsyncMock(
        return_value=SimpleNamespace(
            id=50
        )
    )

    monkeypatch.setattr(
        api,
        "_get_trade_document_direction",
        AsyncMock(
            return_value=TradeDirection.SALE
        ),
    )

    monkeypatch.setattr(
        api,
        "confirm_purchase_order",
        purchase,
    )

    monkeypatch.setattr(
        api,
        "confirm_sales_order",
        sales,
    )

    monkeypatch.setattr(
        api,
        "_load_trade_document",
        AsyncMock(
            return_value=object()
        ),
    )

    await api.confirm_trade_document_sales_order(
        company_id=1,
        document_id=50,
        db=db,
        _permission=None,
    )

    sales.assert_awaited_once_with(
        db,
        company_id=1,
        document_id=50,
    )

    purchase.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_dispatches_purchase_order(
    monkeypatch,
):
    db = FakeDB()

    purchase = AsyncMock(
        return_value=SimpleNamespace(
            id=50
        )
    )

    sales = AsyncMock()

    monkeypatch.setattr(
        api,
        "_get_trade_document_direction",
        AsyncMock(
            return_value=TradeDirection.PURCHASE
        ),
    )

    monkeypatch.setattr(
        api,
        "cancel_purchase_order",
        purchase,
    )

    monkeypatch.setattr(
        api,
        "cancel_sales_order",
        sales,
    )

    monkeypatch.setattr(
        api,
        "_load_trade_document",
        AsyncMock(
            return_value=object()
        ),
    )

    await api.cancel_trade_document_sales_order(
        company_id=1,
        document_id=50,
        db=db,
        _permission=None,
    )

    purchase.assert_awaited_once_with(
        db,
        company_id=1,
        document_id=50,
    )

    sales.assert_not_awaited()
    db.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_fulfill_dispatches_purchase_to_receipt_executor(
    monkeypatch,
):
    db = FakeDB()

    purchase = AsyncMock(
        return_value=SimpleNamespace(
            purchase_order=SimpleNamespace(
                id=50
            ),
            warehouse_document=SimpleNamespace(
                id=70,
                document_type=DocumentType.RECEIPT,
            ),
            fulfillment=SimpleNamespace(
                id=80
            ),
            journal_entry=SimpleNamespace(
                id=90
            ),
        )
    )

    sales = AsyncMock()

    reloaded = object()

    monkeypatch.setattr(
        api,
        "_get_trade_document_direction",
        AsyncMock(
            return_value=TradeDirection.PURCHASE
        ),
    )

    monkeypatch.setattr(
        api,
        "execute_purchase_order_fulfillment",
        purchase,
    )

    monkeypatch.setattr(
        api,
        "execute_sales_order_fulfillment",
        sales,
    )

    monkeypatch.setattr(
        api,
        "_load_trade_document",
        AsyncMock(
            return_value=reloaded
        ),
    )

    monkeypatch.setattr(
        api,
        "SalesOrderFulfillmentResponse",
        lambda **kwargs: kwargs,
    )

    response = (
        await api.fulfill_trade_document_sales_order(
            company_id=1,
            document_id=50,
            data=fulfillment_data(),
            current_user=SimpleNamespace(
                id=99
            ),
            db=db,
            _permission=None,
        )
    )

    purchase.assert_awaited_once()

    call = purchase.await_args

    assert call.kwargs[
        "company_id"
    ] == 1

    assert call.kwargs[
        "trade_document_id"
    ] == 50

    assert call.kwargs[
        "created_by"
    ] == 99

    assert len(
        call.kwargs[
            "request_lines"
        ]
    ) == 1

    assert isinstance(
        call.kwargs[
            "request_lines"
        ][0],
        PurchaseOrderFulfillmentRequestLine,
    )

    sales.assert_not_awaited()

    assert response[
        "trade_document"
    ] is reloaded

    assert response[
        "warehouse_document_id"
    ] == 70

    assert response[
        "fulfillment_id"
    ] == 80

    assert response[
        "journal_entry_id"
    ] == 90

    db.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_fulfill_preserves_sales_executor(
    monkeypatch,
):
    db = FakeDB()

    sales = AsyncMock(
        return_value=SimpleNamespace(
            sales_order=SimpleNamespace(
                id=50
            ),
            warehouse_document=SimpleNamespace(
                id=70,
                document_type=DocumentType.ISSUE,
            ),
            fulfillment=SimpleNamespace(
                id=80
            ),
            journal_entry=SimpleNamespace(
                id=90
            ),
        )
    )

    purchase = AsyncMock()

    monkeypatch.setattr(
        api,
        "_get_trade_document_direction",
        AsyncMock(
            return_value=TradeDirection.SALE
        ),
    )

    monkeypatch.setattr(
        api,
        "execute_sales_order_fulfillment",
        sales,
    )

    monkeypatch.setattr(
        api,
        "execute_purchase_order_fulfillment",
        purchase,
    )

    monkeypatch.setattr(
        api,
        "_load_trade_document",
        AsyncMock(
            return_value=object()
        ),
    )

    monkeypatch.setattr(
        api,
        "SalesOrderFulfillmentResponse",
        lambda **kwargs: kwargs,
    )

    await api.fulfill_trade_document_sales_order(
        company_id=1,
        document_id=50,
        data=fulfillment_data(),
        current_user=SimpleNamespace(
            id=99
        ),
        db=db,
        _permission=None,
    )

    sales.assert_awaited_once()

    call = sales.await_args

    assert isinstance(
        call.kwargs[
            "request_lines"
        ][0],
        SalesOrderFulfillmentRequestLine,
    )

    purchase.assert_not_awaited()


@pytest.mark.asyncio
async def test_reverse_dispatches_purchase_fulfillment(
    monkeypatch,
):
    db = FakeDB()

    purchase = AsyncMock(
        return_value=SimpleNamespace(
            purchase_order=SimpleNamespace(
                id=50
            ),
            warehouse_document=SimpleNamespace(
                id=70
            ),
            fulfillment=SimpleNamespace(
                id=80
            ),
        )
    )

    sales = AsyncMock()

    reloaded = object()

    monkeypatch.setattr(
        api,
        "_get_trade_document_direction",
        AsyncMock(
            return_value=TradeDirection.PURCHASE
        ),
    )

    monkeypatch.setattr(
        api,
        "execute_purchase_order_fulfillment_reversal",
        purchase,
    )

    monkeypatch.setattr(
        api,
        "execute_sales_order_fulfillment_reversal",
        sales,
    )

    monkeypatch.setattr(
        api,
        "_load_trade_document",
        AsyncMock(
            return_value=reloaded
        ),
    )

    monkeypatch.setattr(
        api,
        "SalesOrderFulfillmentReversalResponse",
        lambda **kwargs: kwargs,
    )

    response = (
        await api.reverse_trade_document_sales_order_fulfillment(
            company_id=1,
            document_id=50,
            fulfillment_id=80,
            data=SimpleNamespace(
                reversal_date=date(
                    2026,
                    8,
                    28,
                )
            ),
            current_user=SimpleNamespace(
                id=99
            ),
            db=db,
            _permission=None,
        )
    )

    purchase.assert_awaited_once_with(
        db,
        company_id=1,
        trade_document_id=50,
        fulfillment_id=80,
        reversal_date=date(
            2026,
            8,
            28,
        ),
        reversed_by=99,
    )

    sales.assert_not_awaited()

    assert response[
        "trade_document"
    ] is reloaded

    assert response[
        "warehouse_document_id"
    ] == 70

    assert response[
        "fulfillment_id"
    ] == 80

    db.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_reverse_preserves_sales_dispatch(
    monkeypatch,
):
    db = FakeDB()

    sales = AsyncMock(
        return_value=SimpleNamespace(
            sales_order=SimpleNamespace(
                id=50
            ),
            warehouse_document=SimpleNamespace(
                id=70
            ),
            fulfillment=SimpleNamespace(
                id=80
            ),
        )
    )

    purchase = AsyncMock()

    monkeypatch.setattr(
        api,
        "_get_trade_document_direction",
        AsyncMock(
            return_value=TradeDirection.SALE
        ),
    )

    monkeypatch.setattr(
        api,
        "execute_sales_order_fulfillment_reversal",
        sales,
    )

    monkeypatch.setattr(
        api,
        "execute_purchase_order_fulfillment_reversal",
        purchase,
    )

    monkeypatch.setattr(
        api,
        "_load_trade_document",
        AsyncMock(
            return_value=object()
        ),
    )

    monkeypatch.setattr(
        api,
        "SalesOrderFulfillmentReversalResponse",
        lambda **kwargs: kwargs,
    )

    await api.reverse_trade_document_sales_order_fulfillment(
        company_id=1,
        document_id=50,
        fulfillment_id=80,
        data=SimpleNamespace(
            reversal_date=date(
                2026,
                8,
                28,
            )
        ),
        current_user=SimpleNamespace(
            id=99
        ),
        db=db,
        _permission=None,
    )

    sales.assert_awaited_once()
    purchase.assert_not_awaited()
