from datetime import date
from decimal import Decimal

import pytest

from app.models.trade_document import TradeDocument
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.services.trade_document_lifecycle_service import (
    SalesOrderLinesRequiredError,
    SalesOrderStatusError,
    SalesOrderTypeError,
    SalesOrderWarehouseRequiredError,
    reservation_lock_order,
    validate_sales_order_confirmation,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


def _document(
    *,
    direction: TradeDirection = (
        TradeDirection.SALE
    ),
    kind: TradeDocumentKind = (
        TradeDocumentKind.ORDER
    ),
    status: TradeDocumentStatus = (
        TradeDocumentStatus.DRAFT
    ),
) -> TradeDocument:
    return TradeDocument(
        id=100,
        company_id=1,
        counterparty_id=10,
        contract_id=None,
        number="SO-TEST",
        direction=direction,
        kind=kind,
        status=status,
        document_date=date(
            2026,
            8,
            25,
        ),
        currency_code="UAH",
        payment_term_days=0,
        created_by=1,
    )


def _line(
    *,
    line_id: int,
    line_number: int,
    product_id: int,
    warehouse_id: int | None,
) -> TradeDocumentLine:
    return TradeDocumentLine(
        id=line_id,
        company_id=1,
        trade_document_id=100,
        line_number=line_number,
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity=Decimal("2.0000"),
        unit_price=Decimal("100.0000"),
    )


def test_valid_sales_order_can_be_confirmed() -> None:
    document = _document()

    document.lines = [
        _line(
            line_id=1,
            line_number=1,
            product_id=10,
            warehouse_id=20,
        )
    ]

    validate_sales_order_confirmation(
        document
    )


def test_purchase_order_rejected() -> None:
    document = _document(
        direction=TradeDirection.PURCHASE
    )

    document.lines = [
        _line(
            line_id=1,
            line_number=1,
            product_id=10,
            warehouse_id=20,
        )
    ]

    with pytest.raises(
        SalesOrderTypeError
    ):
        validate_sales_order_confirmation(
            document
        )


def test_sales_invoice_rejected() -> None:
    document = _document(
        kind=TradeDocumentKind.INVOICE
    )

    document.lines = [
        _line(
            line_id=1,
            line_number=1,
            product_id=10,
            warehouse_id=20,
        )
    ]

    with pytest.raises(
        SalesOrderTypeError
    ):
        validate_sales_order_confirmation(
            document
        )


def test_confirmed_order_cannot_be_confirmed_again() -> None:
    document = _document(
        status=TradeDocumentStatus.CONFIRMED
    )

    document.lines = [
        _line(
            line_id=1,
            line_number=1,
            product_id=10,
            warehouse_id=20,
        )
    ]

    with pytest.raises(
        SalesOrderStatusError
    ):
        validate_sales_order_confirmation(
            document
        )


def test_cancelled_order_cannot_be_confirmed() -> None:
    document = _document(
        status=TradeDocumentStatus.CANCELLED
    )

    document.lines = [
        _line(
            line_id=1,
            line_number=1,
            product_id=10,
            warehouse_id=20,
        )
    ]

    with pytest.raises(
        SalesOrderStatusError
    ):
        validate_sales_order_confirmation(
            document
        )


def test_empty_sales_order_rejected() -> None:
    document = _document()

    document.lines = []

    with pytest.raises(
        SalesOrderLinesRequiredError
    ):
        validate_sales_order_confirmation(
            document
        )


def test_missing_warehouse_rejected() -> None:
    document = _document()

    document.lines = [
        _line(
            line_id=1,
            line_number=1,
            product_id=10,
            warehouse_id=None,
        )
    ]

    with pytest.raises(
        SalesOrderWarehouseRequiredError
    ):
        validate_sales_order_confirmation(
            document
        )


def test_reservation_lock_order_is_deterministic() -> None:
    document = _document()

    line_a = _line(
        line_id=30,
        line_number=1,
        product_id=20,
        warehouse_id=2,
    )

    line_b = _line(
        line_id=20,
        line_number=2,
        product_id=30,
        warehouse_id=1,
    )

    line_c = _line(
        line_id=10,
        line_number=3,
        product_id=10,
        warehouse_id=1,
    )

    line_d = _line(
        line_id=5,
        line_number=4,
        product_id=10,
        warehouse_id=1,
    )

    document.lines = [
        line_a,
        line_b,
        line_c,
        line_d,
    ]

    ordered = reservation_lock_order(
        document
    )

    assert [
        line.id
        for line in ordered
    ] == [
        5,
        10,
        20,
        30,
    ]
