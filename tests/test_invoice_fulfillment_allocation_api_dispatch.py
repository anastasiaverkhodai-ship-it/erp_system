from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.v1.trade_documents as api

from app.services.invoice_fulfillment_allocation_service import (
    DuplicateActiveInvoiceFulfillmentAllocationError,
    FulfillmentOverAllocationError,
    InvoiceFulfillmentAllocationNotFoundError,
    InvoiceFulfillmentAllocationProductError,
    InvoiceFulfillmentAllocationReversalStateError,
    InvoiceFulfillmentAllocationStatusError,
    InvoiceOverAllocationError,
)


class FakeDB:
    def __init__(self):
        self.commit = AsyncMock()
        self.rollback = AsyncMock()


def current_user():
    return SimpleNamespace(
        id=77
    )


def create_request():
    return SimpleNamespace(
        invoice_line_id=10,
        fulfillment_id=20,
        fulfillment_line_id=30,
        quantity=Decimal("2.0000"),
    )


@pytest.mark.parametrize(
    (
        "error",
        "expected_status",
    ),
    [
        (
            InvoiceFulfillmentAllocationNotFoundError(
                "not found"
            ),
            404,
        ),
        (
            InvoiceFulfillmentAllocationProductError(
                "product mismatch"
            ),
            422,
        ),
        (
            InvoiceFulfillmentAllocationStatusError(
                "invalid state"
            ),
            409,
        ),
        (
            DuplicateActiveInvoiceFulfillmentAllocationError(
                "duplicate"
            ),
            409,
        ),
        (
            InvoiceOverAllocationError(
                "invoice over-allocation"
            ),
            409,
        ),
        (
            FulfillmentOverAllocationError(
                "fulfillment over-allocation"
            ),
            409,
        ),
        (
            InvoiceFulfillmentAllocationReversalStateError(
                "already reversed"
            ),
            409,
        ),
    ],
)
def test_allocation_error_mapping(
    error,
    expected_status,
):
    result = (
        api._invoice_fulfillment_http_exception(
            error
        )
    )

    assert (
        result.status_code
        == expected_status
    )

    assert result.detail == str(
        error
    )


@pytest.mark.asyncio
async def test_create_allocation_dispatch(
    monkeypatch,
):
    db = FakeDB()

    allocation = SimpleNamespace(
        id=100
    )

    create = AsyncMock(
        return_value=allocation
    )

    monkeypatch.setattr(
        api,
        "create_invoice_fulfillment_allocation",
        create,
    )

    result = (
        await api.create_trade_invoice_fulfillment_allocation(
            company_id=1,
            invoice_id=2,
            data=create_request(),
            current_user=current_user(),
            db=db,
            _permission=None,
        )
    )

    assert result is allocation

    create.assert_awaited_once_with(
        db,
        company_id=1,
        invoice_id=2,
        invoice_line_id=10,
        fulfillment_id=20,
        fulfillment_line_id=30,
        quantity=Decimal("2.0000"),
        created_by=77,
    )

    db.commit.assert_awaited_once_with()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_allocation_service_error_rolls_back(
    monkeypatch,
):
    db = FakeDB()

    create = AsyncMock(
        side_effect=InvoiceOverAllocationError(
            "too much"
        )
    )

    monkeypatch.setattr(
        api,
        "create_invoice_fulfillment_allocation",
        create,
    )

    with pytest.raises(
        HTTPException
    ) as exc:
        await api.create_trade_invoice_fulfillment_allocation(
            company_id=1,
            invoice_id=2,
            data=create_request(),
            current_user=current_user(),
            db=db,
            _permission=None,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "too much"

    db.rollback.assert_awaited_once_with()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reverse_allocation_dispatch(
    monkeypatch,
):
    db = FakeDB()

    allocation = SimpleNamespace(
        id=100
    )

    reverse = AsyncMock(
        return_value=allocation
    )

    monkeypatch.setattr(
        api,
        "reverse_invoice_fulfillment_allocation",
        reverse,
    )

    result = (
        await api.reverse_trade_invoice_fulfillment_allocation(
            company_id=1,
            invoice_id=2,
            allocation_id=100,
            current_user=current_user(),
            db=db,
            _permission=None,
        )
    )

    assert result is allocation

    reverse.assert_awaited_once_with(
        db,
        company_id=1,
        invoice_id=2,
        allocation_id=100,
        reversed_by=77,
    )

    db.commit.assert_awaited_once_with()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_reverse_allocation_error_rolls_back(
    monkeypatch,
):
    db = FakeDB()

    reverse = AsyncMock(
        side_effect=(
            InvoiceFulfillmentAllocationReversalStateError(
                "already reversed"
            )
        )
    )

    monkeypatch.setattr(
        api,
        "reverse_invoice_fulfillment_allocation",
        reverse,
    )

    with pytest.raises(
        HTTPException
    ) as exc:
        await api.reverse_trade_invoice_fulfillment_allocation(
            company_id=1,
            invoice_id=2,
            allocation_id=100,
            current_user=current_user(),
            db=db,
            _permission=None,
        )

    assert exc.value.status_code == 409

    db.rollback.assert_awaited_once_with()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_allocation_history_dispatch(
    monkeypatch,
):
    db = FakeDB()

    allocations = (
        SimpleNamespace(id=1),
        SimpleNamespace(id=2),
    )

    history = AsyncMock(
        return_value=(
            SimpleNamespace(id=50),
            allocations,
        )
    )

    monkeypatch.setattr(
        api,
        "get_invoice_fulfillment_allocation_history",
        history,
    )

    result = (
        await api.list_trade_invoice_fulfillment_allocations(
            company_id=1,
            invoice_id=50,
            db=db,
            _permission=None,
        )
    )

    assert result == list(
        allocations
    )

    history.assert_awaited_once_with(
        db,
        company_id=1,
        invoice_id=50,
    )

    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_wrong_document_type_maps_to_422(
    monkeypatch,
):
    db = FakeDB()

    history = AsyncMock(
        side_effect=(
            InvoiceFulfillmentAllocationProductError(
                "invalid invoice source"
            )
        )
    )

    monkeypatch.setattr(
        api,
        "get_invoice_fulfillment_allocation_history",
        history,
    )

    with pytest.raises(
        HTTPException
    ) as exc:
        await api.list_trade_invoice_fulfillment_allocations(
            company_id=1,
            invoice_id=50,
            db=db,
            _permission=None,
        )

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_reconciliation_dispatch(
    monkeypatch,
):
    db = FakeDB()

    allocation = SimpleNamespace(
        id=91,
        invoice_line_id=11,
        fulfillment_id=21,
        fulfillment_line_id=31,
        order_id=41,
        order_line_id=51,
        product_id=61,
        quantity=Decimal("2.0000"),
        status="active",
        created_by=77,
        created_at=datetime.now(timezone.utc),
        reversed_by=None,
        reversed_at=None,
    )

    allocation_view = SimpleNamespace(
        allocation=allocation,
        fulfillment_line_quantity=(
            Decimal("5.0000")
        ),
        fulfillment_line_active_allocated_quantity=(
            Decimal("2.0000")
        ),
        fulfillment_line_remaining_quantity=(
            Decimal("3.0000")
        ),
    )

    invoice_line = SimpleNamespace(
        id=11,
        line_number=1,
        product_id=61,
        warehouse_id=None,
        quantity=Decimal("5.0000"),
    )

    line = SimpleNamespace(
        invoice_line=invoice_line,
        active_allocated_quantity=(
            Decimal("2.0000")
        ),
        remaining_quantity=(
            Decimal("3.0000")
        ),
        fully_allocated=False,
        allocations=(
            allocation_view,
        ),
    )

    invoice = SimpleNamespace(
        company_id=1,
        id=50,
        direction="purchase",
        status="confirmed",
        counterparty_id=70,
        contract_id=None,
        currency_code="UAH",
    )

    reconciliation = SimpleNamespace(
        invoice=invoice,
        lines=(
            line,
        ),
        fully_allocated=False,
    )

    read = AsyncMock(
        return_value=reconciliation
    )

    monkeypatch.setattr(
        api,
        "get_invoice_fulfillment_reconciliation",
        read,
    )

    result = (
        await api.get_trade_invoice_fulfillment_reconciliation(
            company_id=1,
            invoice_id=50,
            db=db,
            _permission=None,
        )
    )

    assert result.company_id == 1
    assert result.invoice_id == 50

    assert not result.fully_allocated

    assert (
        result.lines[0]
        .active_allocated_quantity
        == Decimal("2.0000")
    )

    assert (
        result.lines[0]
        .remaining_quantity
        == Decimal("3.0000")
    )

    assert len(
        result.lines[0].allocations
    ) == 1

    read.assert_awaited_once_with(
        db,
        company_id=1,
        invoice_id=50,
    )

    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
