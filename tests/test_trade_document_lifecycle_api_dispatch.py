from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.v1.trade_documents as api
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
)


class FakeDB:
    def __init__(self):
        self.commit = AsyncMock()
        self.rollback = AsyncMock()


def patch_identity(
    monkeypatch,
    *,
    direction,
    kind,
):
    monkeypatch.setattr(
        api,
        "_get_trade_document_lifecycle_identity",
        AsyncMock(
            return_value=(
                direction,
                kind,
            )
        ),
    )


def fulfill_data():
    return SimpleNamespace(
        warehouse_document_number="WD-1",
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
@pytest.mark.parametrize(
    (
        "direction",
        "kind",
        "expected_name",
    ),
    [
        (
            TradeDirection.SALE,
            TradeDocumentKind.ORDER,
            "confirm_sales_order",
        ),
        (
            TradeDirection.PURCHASE,
            TradeDocumentKind.ORDER,
            "confirm_purchase_order",
        ),
        (
            TradeDirection.SALE,
            TradeDocumentKind.INVOICE,
            "confirm_sales_invoice",
        ),
        (
            TradeDirection.PURCHASE,
            TradeDocumentKind.INVOICE,
            "confirm_purchase_invoice",
        ),
    ],
)
async def test_confirm_dispatch(
    monkeypatch,
    direction,
    kind,
    expected_name,
):
    db = FakeDB()

    patch_identity(
        monkeypatch,
        direction=direction,
        kind=kind,
    )

    names = (
        "confirm_sales_order",
        "confirm_purchase_order",
        "confirm_sales_invoice",
        "confirm_purchase_invoice",
    )

    mocks = {}

    for name in names:
        mock = AsyncMock(
            return_value=SimpleNamespace(
                id=50
            )
        )

        mocks[name] = mock

        monkeypatch.setattr(
            api,
            name,
            mock,
        )

    reloaded = object()

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

    mocks[
        expected_name
    ].assert_awaited_once_with(
        db,
        company_id=1,
        document_id=50,
    )

    for name, mock in mocks.items():
        if name != expected_name:
            mock.assert_not_awaited()

    db.commit.assert_awaited_once_with()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "direction",
        "kind",
        "expected_name",
    ),
    [
        (
            TradeDirection.SALE,
            TradeDocumentKind.ORDER,
            "cancel_sales_order",
        ),
        (
            TradeDirection.PURCHASE,
            TradeDocumentKind.ORDER,
            "cancel_purchase_order",
        ),
        (
            TradeDirection.SALE,
            TradeDocumentKind.INVOICE,
            "cancel_sales_invoice",
        ),
        (
            TradeDirection.PURCHASE,
            TradeDocumentKind.INVOICE,
            "cancel_purchase_invoice",
        ),
    ],
)
async def test_cancel_dispatch(
    monkeypatch,
    direction,
    kind,
    expected_name,
):
    db = FakeDB()

    patch_identity(
        monkeypatch,
        direction=direction,
        kind=kind,
    )

    names = (
        "cancel_sales_order",
        "cancel_purchase_order",
        "cancel_sales_invoice",
        "cancel_purchase_invoice",
    )

    mocks = {}

    for name in names:
        mock = AsyncMock(
            return_value=SimpleNamespace(
                id=50
            )
        )

        mocks[name] = mock

        monkeypatch.setattr(
            api,
            name,
            mock,
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

    mocks[
        expected_name
    ].assert_awaited_once_with(
        db,
        company_id=1,
        document_id=50,
    )

    for name, mock in mocks.items():
        if name != expected_name:
            mock.assert_not_awaited()

    db.commit.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "direction",
    [
        TradeDirection.SALE,
        TradeDirection.PURCHASE,
    ],
)
async def test_fulfill_rejects_invoice_before_executor(
    monkeypatch,
    direction,
):
    db = FakeDB()

    patch_identity(
        monkeypatch,
        direction=direction,
        kind=TradeDocumentKind.INVOICE,
    )

    sales = AsyncMock()
    purchase = AsyncMock()

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

    with pytest.raises(
        HTTPException
    ) as exc:
        await api.fulfill_trade_document_sales_order(
            company_id=1,
            document_id=50,
            data=fulfill_data(),
            current_user=SimpleNamespace(
                id=99
            ),
            db=db,
            _permission=None,
        )

    assert (
        exc.value.status_code
        == 422
    )

    assert (
        exc.value.detail
        == "Only trade document kind 'order' can be fulfilled"
    )

    sales.assert_not_awaited()
    purchase.assert_not_awaited()

    db.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "direction",
    [
        TradeDirection.SALE,
        TradeDirection.PURCHASE,
    ],
)
async def test_fulfillment_reversal_rejects_invoice(
    monkeypatch,
    direction,
):
    db = FakeDB()

    patch_identity(
        monkeypatch,
        direction=direction,
        kind=TradeDocumentKind.INVOICE,
    )

    sales = AsyncMock()
    purchase = AsyncMock()

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

    with pytest.raises(
        HTTPException
    ) as exc:
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

    assert (
        exc.value.status_code
        == 422
    )

    assert (
        exc.value.detail
        == (
            "Only trade document kind 'order' "
            "can have fulfillment reversal"
        )
    )

    sales.assert_not_awaited()
    purchase.assert_not_awaited()

    db.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_confirm_sales_order_credit_limit_exceeded_maps_to_422(
    monkeypatch,
):
    from app.api.v1 import trade_documents as api
    from app.services.sales_credit_control_service import (
        SalesCreditLimitExceededError,
    )

    class _DB:
        def __init__(self):
            self.rollback_called = False
            self.commit_called = False

        async def rollback(self):
            self.rollback_called = True

        async def commit(self):
            self.commit_called = True

    db = _DB()

    async def _identity(*args, **kwargs):
        return (
            api.TradeDirection.SALE,
            api.TradeDocumentKind.ORDER,
        )

    async def _confirm(*args, **kwargs):
        raise SalesCreditLimitExceededError(
            "Sales credit limit exceeded"
        )

    monkeypatch.setattr(
        api,
        "_get_trade_document_lifecycle_identity",
        _identity,
    )

    monkeypatch.setattr(
        api,
        "confirm_sales_order",
        _confirm,
    )

    with pytest.raises(
        api.HTTPException
    ) as exc_info:
        await api.confirm_trade_document_sales_order(
            company_id=1,
            document_id=2,
            db=db,
            _permission=None,
        )

    assert exc_info.value.status_code == 422
    assert (
        exc_info.value.detail
        == "Sales credit limit exceeded"
    )
    assert db.rollback_called is True
    assert db.commit_called is False


@pytest.mark.asyncio
async def test_confirm_sales_order_credit_data_integrity_maps_to_409(
    monkeypatch,
):
    from app.api.v1 import trade_documents as api
    from app.services.sales_credit_control_service import (
        SalesCreditDataIntegrityError,
    )

    class _DB:
        def __init__(self):
            self.rollback_called = False
            self.commit_called = False

        async def rollback(self):
            self.rollback_called = True

        async def commit(self):
            self.commit_called = True

    db = _DB()

    async def _identity(*args, **kwargs):
        return (
            api.TradeDirection.SALE,
            api.TradeDocumentKind.ORDER,
        )

    async def _confirm(*args, **kwargs):
        raise SalesCreditDataIntegrityError(
            "Credit exposure currency mismatch"
        )

    monkeypatch.setattr(
        api,
        "_get_trade_document_lifecycle_identity",
        _identity,
    )

    monkeypatch.setattr(
        api,
        "confirm_sales_order",
        _confirm,
    )

    with pytest.raises(
        api.HTTPException
    ) as exc_info:
        await api.confirm_trade_document_sales_order(
            company_id=1,
            document_id=2,
            db=db,
            _permission=None,
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail
        == "Credit exposure currency mismatch"
    )
    assert db.rollback_called is True
    assert db.commit_called is False
