from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.document import (
    DocumentStatus,
    DocumentType,
)
from app.services.invoice_fulfillment_allocation_service import (
    FulfillmentOverAllocationError,
    InvoiceFulfillmentAllocationContractError,
    InvoiceFulfillmentAllocationCounterpartyError,
    InvoiceFulfillmentAllocationCurrencyError,
    InvoiceFulfillmentAllocationDirectionError,
    InvoiceFulfillmentAllocationProductError,
    InvoiceFulfillmentAllocationQuantityError,
    InvoiceFulfillmentAllocationStatusError,
    InvoiceFulfillmentAllocationTypeError,
    InvoiceFulfillmentAllocationWarehouseError,
    InvoiceFulfillmentMatchContext,
    InvoiceOverAllocationError,
    create_invoice_fulfillment_allocation_plan,
    get_expected_fulfillment_document_type,
    validate_invoice_fulfillment_match,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


def make_context(
    *,
    direction=TradeDirection.SALE,
):
    expected_type = (
        DocumentType.ISSUE
        if direction == TradeDirection.SALE
        else DocumentType.RECEIPT
    )

    invoice = SimpleNamespace(
        kind=TradeDocumentKind.INVOICE,
        status=TradeDocumentStatus.CONFIRMED,
        direction=direction,
        counterparty_id=10,
        contract_id=20,
        currency_code="UAH",
    )

    invoice_line = SimpleNamespace(
        product_id=30,
        warehouse_id=40,
        quantity=Decimal("10"),
    )

    order = SimpleNamespace(
        kind=TradeDocumentKind.ORDER,
        status=TradeDocumentStatus.FULFILLED,
        direction=direction,
        counterparty_id=10,
        contract_id=20,
        currency_code="UAH",
    )

    fulfillment = SimpleNamespace(
        warehouse_document_type=(
            expected_type.value
        ),
    )

    fulfillment_line = SimpleNamespace(
        product_id=30,
        warehouse_id=40,
        quantity=Decimal("10"),
    )

    warehouse_document = SimpleNamespace(
        document_type=expected_type,
        status=DocumentStatus.POSTED,
    )

    warehouse_line = SimpleNamespace(
        product_id=30,
        quantity=Decimal("10"),
    )

    return InvoiceFulfillmentMatchContext(
        invoice=invoice,
        invoice_line=invoice_line,
        order=order,
        fulfillment=fulfillment,
        fulfillment_line=fulfillment_line,
        warehouse_document=warehouse_document,
        warehouse_line=warehouse_line,
    )


@pytest.mark.parametrize(
    (
        "direction",
        "expected",
    ),
    [
        (
            TradeDirection.SALE,
            DocumentType.ISSUE,
        ),
        (
            TradeDirection.PURCHASE,
            DocumentType.RECEIPT,
        ),
    ],
)
def test_expected_fulfillment_type(
    direction,
    expected,
):
    assert (
        get_expected_fulfillment_document_type(
            direction
        )
        == expected
    )


@pytest.mark.parametrize(
    "direction",
    [
        TradeDirection.SALE,
        TradeDirection.PURCHASE,
    ],
)
def test_valid_match(direction):
    context = make_context(
        direction=direction
    )

    validate_invoice_fulfillment_match(
        context
    )


def test_rejects_non_invoice():
    context = make_context()

    context.invoice.kind = (
        TradeDocumentKind.ORDER
    )

    with pytest.raises(
        InvoiceFulfillmentAllocationTypeError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_rejects_non_confirmed_invoice():
    context = make_context()

    context.invoice.status = (
        TradeDocumentStatus.DRAFT
    )

    with pytest.raises(
        InvoiceFulfillmentAllocationStatusError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_rejects_non_order_fulfillment_source():
    context = make_context()

    context.order.kind = (
        TradeDocumentKind.INVOICE
    )

    with pytest.raises(
        InvoiceFulfillmentAllocationTypeError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_rejects_unfulfilled_order_state():
    context = make_context()

    context.order.status = (
        TradeDocumentStatus.CONFIRMED
    )

    with pytest.raises(
        InvoiceFulfillmentAllocationStatusError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_rejects_direction_mismatch():
    context = make_context()

    context.order.direction = (
        TradeDirection.PURCHASE
    )

    with pytest.raises(
        InvoiceFulfillmentAllocationDirectionError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_rejects_counterparty_mismatch():
    context = make_context()

    context.order.counterparty_id = 999

    with pytest.raises(
        InvoiceFulfillmentAllocationCounterpartyError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_rejects_contract_mismatch():
    context = make_context()

    context.order.contract_id = 999

    with pytest.raises(
        InvoiceFulfillmentAllocationContractError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_rejects_currency_mismatch():
    context = make_context()

    context.order.currency_code = "EUR"

    with pytest.raises(
        InvoiceFulfillmentAllocationCurrencyError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_rejects_product_mismatch():
    context = make_context()

    context.fulfillment_line.product_id = 999

    with pytest.raises(
        InvoiceFulfillmentAllocationProductError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_rejects_reversed_warehouse_document():
    context = make_context()

    context.warehouse_document.status = (
        DocumentStatus.REVERSED
    )

    with pytest.raises(
        InvoiceFulfillmentAllocationStatusError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_rejects_wrong_warehouse_document_type():
    context = make_context()

    context.warehouse_document.document_type = (
        DocumentType.RECEIPT
    )

    with pytest.raises(
        InvoiceFulfillmentAllocationWarehouseError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_rejects_fulfillment_quantity_mismatch():
    context = make_context()

    context.warehouse_line.quantity = (
        Decimal("9")
    )

    with pytest.raises(
        InvoiceFulfillmentAllocationWarehouseError
    ):
        validate_invoice_fulfillment_match(
            context
        )


def test_allocation_plan_valid_partial():
    plan = (
        create_invoice_fulfillment_allocation_plan(
            invoice_line_quantity=Decimal("10"),
            fulfillment_line_quantity=Decimal("8"),
            requested_quantity=Decimal("3"),
            invoice_allocated_before=Decimal("2"),
            fulfillment_allocated_before=Decimal("1"),
        )
    )

    assert plan.quantity == Decimal("3")

    assert (
        plan.invoice_allocated_before
        == Decimal("2")
    )

    assert (
        plan.invoice_allocated_after
        == Decimal("5")
    )

    assert (
        plan.fulfillment_allocated_before
        == Decimal("1")
    )

    assert (
        plan.fulfillment_allocated_after
        == Decimal("4")
    )


def test_allocation_plan_exact_remaining_quantity():
    plan = (
        create_invoice_fulfillment_allocation_plan(
            invoice_line_quantity=Decimal("10"),
            fulfillment_line_quantity=Decimal("10"),
            requested_quantity=Decimal("4"),
            invoice_allocated_before=Decimal("6"),
            fulfillment_allocated_before=Decimal("6"),
        )
    )

    assert (
        plan.invoice_allocated_after
        == Decimal("10")
    )

    assert (
        plan.fulfillment_allocated_after
        == Decimal("10")
    )


def test_rejects_zero_quantity():
    with pytest.raises(
        InvoiceFulfillmentAllocationQuantityError
    ):
        create_invoice_fulfillment_allocation_plan(
            invoice_line_quantity=Decimal("10"),
            fulfillment_line_quantity=Decimal("10"),
            requested_quantity=Decimal("0"),
            invoice_allocated_before=Decimal("0"),
            fulfillment_allocated_before=Decimal("0"),
        )


def test_rejects_invoice_over_allocation():
    with pytest.raises(
        InvoiceOverAllocationError
    ):
        create_invoice_fulfillment_allocation_plan(
            invoice_line_quantity=Decimal("10"),
            fulfillment_line_quantity=Decimal("20"),
            requested_quantity=Decimal("3"),
            invoice_allocated_before=Decimal("8"),
            fulfillment_allocated_before=Decimal("5"),
        )


def test_rejects_fulfillment_over_allocation():
    with pytest.raises(
        FulfillmentOverAllocationError
    ):
        create_invoice_fulfillment_allocation_plan(
            invoice_line_quantity=Decimal("20"),
            fulfillment_line_quantity=Decimal("10"),
            requested_quantity=Decimal("3"),
            invoice_allocated_before=Decimal("5"),
            fulfillment_allocated_before=Decimal("8"),
        )


def test_rejects_corrupt_existing_invoice_allocation():
    with pytest.raises(
        InvoiceOverAllocationError
    ):
        create_invoice_fulfillment_allocation_plan(
            invoice_line_quantity=Decimal("10"),
            fulfillment_line_quantity=Decimal("20"),
            requested_quantity=Decimal("1"),
            invoice_allocated_before=Decimal("11"),
            fulfillment_allocated_before=Decimal("5"),
        )


def test_rejects_corrupt_existing_fulfillment_allocation():
    with pytest.raises(
        FulfillmentOverAllocationError
    ):
        create_invoice_fulfillment_allocation_plan(
            invoice_line_quantity=Decimal("20"),
            fulfillment_line_quantity=Decimal("10"),
            requested_quantity=Decimal("1"),
            invoice_allocated_before=Decimal("5"),
            fulfillment_allocated_before=Decimal("11"),
        )


def test_invoice_warehouse_null_is_allowed():
    context = make_context()

    context.invoice_line.warehouse_id = None

    validate_invoice_fulfillment_match(
        context
    )


def test_invoice_warehouse_same_as_fulfillment_is_allowed():
    context = make_context()

    context.invoice_line.warehouse_id = 40
    context.fulfillment_line.warehouse_id = 40

    validate_invoice_fulfillment_match(
        context
    )


def test_rejects_invoice_fulfillment_warehouse_mismatch():
    context = make_context()

    context.invoice_line.warehouse_id = 40
    context.fulfillment_line.warehouse_id = 999

    with pytest.raises(
        InvoiceFulfillmentAllocationWarehouseError,
        match=(
            "Invoice line warehouse does not match "
            "fulfillment line warehouse"
        ),
    ):
        validate_invoice_fulfillment_match(
            context
        )
