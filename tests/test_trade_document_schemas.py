from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.trade_document import (
    TradeDocumentCreate,
    TradeDocumentLineCreate,
    TradeDocumentUpdate,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
)


def _valid_create_payload() -> dict:
    return {
        "number": "SO-001",
        "direction": "sale",
        "kind": "order",
        "document_date": "2026-08-25",
        "counterparty_id": 1,
        "lines": [
            {
                "product_id": 1,
                "quantity": "2.5000",
                "unit_price": "100.00",
            }
        ],
    }


def test_trade_document_create_defaults() -> None:
    data = TradeDocumentCreate(
        **_valid_create_payload()
    )

    assert (
        data.direction
        == TradeDirection.SALE
    )

    assert (
        data.kind
        == TradeDocumentKind.ORDER
    )

    assert (
        data.document_date
        == date(2026, 8, 25)
    )

    assert data.contract_id is None
    assert data.currency_code == "UAH"
    assert data.payment_term_days == 0


def test_trade_document_number_normalized() -> None:
    payload = _valid_create_payload()
    payload["number"] = "  SO-002  "

    data = TradeDocumentCreate(
        **payload
    )

    assert data.number == "SO-002"


def test_trade_document_blank_number_rejected() -> None:
    payload = _valid_create_payload()
    payload["number"] = "   "

    with pytest.raises(
        ValidationError
    ):
        TradeDocumentCreate(
            **payload
        )


def test_trade_document_currency_normalized() -> None:
    payload = _valid_create_payload()
    payload["currency_code"] = " eur "

    data = TradeDocumentCreate(
        **payload
    )

    assert data.currency_code == "EUR"


def test_trade_document_unknown_currency_rejected() -> None:
    payload = _valid_create_payload()
    payload["currency_code"] = "XYZ"

    with pytest.raises(
        ValidationError
    ):
        TradeDocumentCreate(
            **payload
        )


def test_trade_document_requires_positive_counterparty() -> None:
    payload = _valid_create_payload()
    payload["counterparty_id"] = 0

    with pytest.raises(
        ValidationError
    ):
        TradeDocumentCreate(
            **payload
        )


def test_trade_document_requires_lines() -> None:
    payload = _valid_create_payload()
    payload["lines"] = []

    with pytest.raises(
        ValidationError
    ):
        TradeDocumentCreate(
            **payload
        )


def test_trade_document_line_validation() -> None:
    line = TradeDocumentLineCreate(
        product_id=1,
        warehouse_id=None,
        quantity=Decimal("2.5000"),
        unit_price=Decimal("10.2500"),
    )

    assert line.product_id == 1
    assert line.warehouse_id is None
    assert line.quantity == Decimal(
        "2.5000"
    )
    assert line.unit_price == Decimal(
        "10.2500"
    )


def test_trade_document_line_bad_quantity_rejected() -> None:
    with pytest.raises(
        ValidationError
    ):
        TradeDocumentLineCreate(
            product_id=1,
            quantity=0,
        )


def test_trade_document_line_negative_price_rejected() -> None:
    with pytest.raises(
        ValidationError
    ):
        TradeDocumentLineCreate(
            product_id=1,
            quantity=1,
            unit_price="-0.01",
        )


def test_trade_document_update_can_clear_contract() -> None:
    data = TradeDocumentUpdate(
        contract_id=None
    )

    dumped = data.model_dump(
        exclude_unset=True
    )

    assert dumped == {
        "contract_id": None,
    }


def test_trade_document_update_omitted_contract_stays_omitted() -> None:
    data = TradeDocumentUpdate(
        number="SO-003"
    )

    dumped = data.model_dump(
        exclude_unset=True
    )

    assert (
        "contract_id"
        not in dumped
    )


def test_trade_document_input_does_not_expose_status() -> None:
    assert (
        "status"
        not in TradeDocumentCreate.model_fields
    )

    assert (
        "status"
        not in TradeDocumentUpdate.model_fields
    )
