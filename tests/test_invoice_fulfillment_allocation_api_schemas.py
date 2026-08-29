from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocationCreateRequest,
    InvoiceFulfillmentAllocationResponse,
    InvoiceFulfillmentReconciliationAllocationResponse,
    InvoiceFulfillmentReconciliationLineResponse,
    InvoiceFulfillmentReconciliationResponse,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentStatus,
)


def test_create_request_contract():
    data = InvoiceFulfillmentAllocationCreateRequest(
        invoice_line_id=10,
        fulfillment_id=20,
        fulfillment_line_id=30,
        quantity=Decimal("2.5000"),
    )

    assert data.invoice_line_id == 10
    assert data.fulfillment_id == 20
    assert data.fulfillment_line_id == 30
    assert data.quantity == Decimal("2.5000")


@pytest.mark.parametrize(
    "field",
    [
        "product_id",
        "order_id",
        "warehouse_id",
        "amount",
        "status",
        "created_by",
    ],
)
def test_create_request_forbids_derived_fields(
    field,
):
    payload = {
        "invoice_line_id": 10,
        "fulfillment_id": 20,
        "fulfillment_line_id": 30,
        "quantity": "2.0000",
        field: 999,
    }

    with pytest.raises(
        ValidationError
    ):
        InvoiceFulfillmentAllocationCreateRequest(
            **payload
        )


def test_create_request_rejects_non_positive_quantity():
    with pytest.raises(
        ValidationError
    ):
        InvoiceFulfillmentAllocationCreateRequest(
            invoice_line_id=10,
            fulfillment_id=20,
            fulfillment_line_id=30,
            quantity=Decimal("0"),
        )


def test_allocation_response_contract():
    fields = set(
        InvoiceFulfillmentAllocationResponse.model_fields
    )

    assert fields == {
        "id",
        "company_id",
        "invoice_id",
        "invoice_line_id",
        "fulfillment_id",
        "fulfillment_line_id",
        "order_id",
        "order_line_id",
        "product_id",
        "quantity",
        "status",
        "created_by",
        "created_at",
        "reversed_by",
        "reversed_at",
    }


def test_reconciliation_contract():
    now = datetime.now(
        timezone.utc
    )

    allocation = (
        InvoiceFulfillmentReconciliationAllocationResponse(
            id=1,
            invoice_line_id=10,
            fulfillment_id=20,
            fulfillment_line_id=30,
            order_id=40,
            order_line_id=50,
            product_id=60,
            quantity=Decimal("2.0000"),
            status=(
                InvoiceFulfillmentAllocationStatus.ACTIVE
            ),
            fulfillment_line_quantity=(
                Decimal("5.0000")
            ),
            fulfillment_line_active_allocated_quantity=(
                Decimal("2.0000")
            ),
            fulfillment_line_remaining_quantity=(
                Decimal("3.0000")
            ),
            created_by=1,
            created_at=now,
            reversed_by=None,
            reversed_at=None,
        )
    )

    line = (
        InvoiceFulfillmentReconciliationLineResponse(
            invoice_line_id=10,
            line_number=1,
            product_id=60,
            warehouse_id=None,
            invoice_quantity=Decimal("5.0000"),
            active_allocated_quantity=(
                Decimal("2.0000")
            ),
            remaining_quantity=Decimal("3.0000"),
            fully_allocated=False,
            allocations=[
                allocation
            ],
        )
    )

    response = (
        InvoiceFulfillmentReconciliationResponse(
            company_id=1,
            invoice_id=2,
            direction=TradeDirection.PURCHASE,
            status=TradeDocumentStatus.CONFIRMED,
            counterparty_id=3,
            contract_id=None,
            currency_code="UAH",
            fully_allocated=False,
            lines=[
                line
            ],
        )
    )

    assert not response.fully_allocated

    assert (
        response.lines[0]
        .remaining_quantity
        == Decimal("3.0000")
    )

    assert (
        response.lines[0]
        .allocations[0]
        .fulfillment_line_remaining_quantity
        == Decimal("3.0000")
    )
