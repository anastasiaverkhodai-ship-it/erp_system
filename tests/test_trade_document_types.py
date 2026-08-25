from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


def test_trade_direction_values() -> None:
    assert {
        item.value
        for item in TradeDirection
    } == {
        "sale",
        "purchase",
    }


def test_trade_document_kind_values() -> None:
    assert {
        item.value
        for item in TradeDocumentKind
    } == {
        "order",
        "invoice",
    }


def test_trade_document_status_values() -> None:
    assert {
        item.value
        for item in TradeDocumentStatus
    } == {
        "draft",
        "confirmed",
        "partially_fulfilled",
        "fulfilled",
        "cancelled",
    }
