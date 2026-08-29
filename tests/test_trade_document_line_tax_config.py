from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.schemas.trade_document import (
    TradeDocumentLineCreate,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)


def test_trade_document_line_has_tax_config_columns():
    columns = set(
        TradeDocumentLine.__table__.columns.keys()
    )

    assert {
        "tax_rate_code",
        "tax_recognition_method",
    }.issubset(columns)


def test_trade_document_line_tax_checks_exist():
    checks = {
        constraint.name
        for constraint
        in TradeDocumentLine.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert {
        "ck_trade_document_line_tax_config_pair",
        "ck_trade_document_line_tax_rate_code_nonempty",
        "ck_trade_document_line_tax_recognition_method",
    }.issubset(checks)


def test_line_without_tax_config_remains_valid():
    line = TradeDocumentLineCreate(
        product_id=1,
        quantity=Decimal("1.0000"),
        unit_price=Decimal("100.0000"),
    )

    assert line.tax_rate_code is None
    assert line.tax_recognition_method is None


def test_line_accepts_complete_tax_config():
    line = TradeDocumentLineCreate(
        product_id=1,
        quantity=Decimal("1.0000"),
        unit_price=Decimal("100.0000"),
        tax_rate_code=" vat20 ",
        tax_recognition_method="first_event",
        tax_price_mode="exclusive",
    )

    assert line.tax_rate_code == "VAT20"
    assert (
        line.tax_recognition_method
        == TaxRecognitionMethod.FIRST_EVENT
    )


def test_line_rejects_rate_without_recognition_method():
    with pytest.raises(ValidationError):
        TradeDocumentLineCreate(
            product_id=1,
            quantity=1,
            tax_rate_code="VAT20",
        )


def test_line_rejects_method_without_rate():
    with pytest.raises(ValidationError):
        TradeDocumentLineCreate(
            product_id=1,
            quantity=1,
            tax_recognition_method="cash_method",
        )


def test_line_rejects_blank_tax_rate_code():
    with pytest.raises(ValidationError):
        TradeDocumentLineCreate(
            product_id=1,
            quantity=1,
            tax_rate_code="   ",
            tax_recognition_method="first_event",
        )
