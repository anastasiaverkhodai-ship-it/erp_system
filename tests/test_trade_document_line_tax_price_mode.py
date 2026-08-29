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
from app.services.tax_price_types import (
    TaxPriceMode,
)


def test_tax_price_mode_column_exists():
    assert (
        "tax_price_mode"
        in TradeDocumentLine.__table__.columns
    )


def test_tax_price_mode_checks_exist():
    names = {
        constraint.name
        for constraint
        in TradeDocumentLine.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert (
        "ck_trade_document_line_tax_price_config_pair"
        in names
    )

    assert (
        "ck_trade_document_line_tax_price_mode"
        in names
    )


def test_no_tax_configuration_has_no_price_mode():
    line = TradeDocumentLineCreate(
        product_id=1,
        quantity=Decimal("1.0000"),
        unit_price=Decimal("120.0000"),
    )

    assert line.tax_rate_code is None
    assert line.tax_recognition_method is None
    assert line.tax_price_mode is None


def test_tax_exclusive_price_mode():
    line = TradeDocumentLineCreate(
        product_id=1,
        quantity=Decimal("1.0000"),
        unit_price=Decimal("100.0000"),
        tax_rate_code="VAT20",
        tax_recognition_method="first_event",
        tax_price_mode="exclusive",
    )

    assert (
        line.tax_price_mode
        == TaxPriceMode.EXCLUSIVE
    )


def test_tax_inclusive_price_mode():
    line = TradeDocumentLineCreate(
        product_id=1,
        quantity=Decimal("1.0000"),
        unit_price=Decimal("120.0000"),
        tax_rate_code="VAT20",
        tax_recognition_method="first_event",
        tax_price_mode="inclusive",
    )

    assert (
        line.tax_price_mode
        == TaxPriceMode.INCLUSIVE
    )


def test_tax_config_requires_price_mode():
    with pytest.raises(
        ValidationError
    ):
        TradeDocumentLineCreate(
            product_id=1,
            quantity=1,
            tax_rate_code="VAT20",
            tax_recognition_method="first_event",
        )


def test_price_mode_requires_tax_config():
    with pytest.raises(
        ValidationError
    ):
        TradeDocumentLineCreate(
            product_id=1,
            quantity=1,
            tax_price_mode="exclusive",
        )


def test_invalid_price_mode_rejected():
    with pytest.raises(
        ValidationError
    ):
        TradeDocumentLineCreate(
            product_id=1,
            quantity=1,
            tax_rate_code="VAT20",
            tax_recognition_method="first_event",
            tax_price_mode="invalid",
        )
