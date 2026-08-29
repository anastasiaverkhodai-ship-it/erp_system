from datetime import datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentStatus,
)


class InvoiceFulfillmentAllocationCreateRequest(BaseModel):
    """
    Create one quantity-based Invoice <-> Fulfillment match.

    Product/order/company identity is deliberately not accepted
    from the API caller. It is derived and validated from the
    persistent Invoice and Trade Fulfillment records.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    invoice_line_id: int = Field(
        gt=0,
    )

    fulfillment_id: int = Field(
        gt=0,
    )

    fulfillment_line_id: int = Field(
        gt=0,
    )

    quantity: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=4,
    )


class InvoiceFulfillmentAllocationResponse(BaseModel):
    """
    Persistent audit record for one Invoice/Fulfillment match.
    """

    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int

    invoice_id: int
    invoice_line_id: int

    fulfillment_id: int
    fulfillment_line_id: int

    order_id: int
    order_line_id: int

    product_id: int

    quantity: Decimal

    status: InvoiceFulfillmentAllocationStatus

    created_by: int
    created_at: datetime

    reversed_by: int | None
    reversed_at: datetime | None


class InvoiceFulfillmentReconciliationAllocationResponse(
    BaseModel
):
    """
    One historical allocation enriched with the current
    quantity capacity of its persistent Fulfillment line.
    """

    id: int

    invoice_line_id: int

    fulfillment_id: int
    fulfillment_line_id: int

    order_id: int
    order_line_id: int

    product_id: int

    quantity: Decimal

    status: InvoiceFulfillmentAllocationStatus

    fulfillment_line_quantity: Decimal

    fulfillment_line_active_allocated_quantity: Decimal

    fulfillment_line_remaining_quantity: Decimal

    created_by: int
    created_at: datetime

    reversed_by: int | None
    reversed_at: datetime | None


class InvoiceFulfillmentReconciliationLineResponse(
    BaseModel
):
    invoice_line_id: int
    line_number: int

    product_id: int
    warehouse_id: int | None

    invoice_quantity: Decimal

    active_allocated_quantity: Decimal

    remaining_quantity: Decimal

    fully_allocated: bool

    allocations: list[
        InvoiceFulfillmentReconciliationAllocationResponse
    ]


class InvoiceFulfillmentReconciliationResponse(
    BaseModel
):
    company_id: int
    invoice_id: int

    direction: TradeDirection
    status: TradeDocumentStatus

    counterparty_id: int
    contract_id: int | None
    currency_code: str

    fully_allocated: bool

    lines: list[
        InvoiceFulfillmentReconciliationLineResponse
    ]
