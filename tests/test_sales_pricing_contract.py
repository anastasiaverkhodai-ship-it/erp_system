from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
)

from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.schemas.trade_document import (
    TradeDocumentLineCreate,
)


def _constraint_names() -> set[str]:
    return {
        constraint.name
        for constraint
        in TradeDocumentLine.__table__.constraints
        if constraint.name is not None
    }


def test_trade_line_has_price_provenance_columns():
    columns = TradeDocumentLine.__table__.columns

    assert {
        "price_type_code",
        "source_product_price_id",
        "price_uom_code",
        "price_effective_from",
    } <= set(columns.keys())

    assert columns.price_type_code.nullable
    assert columns.source_product_price_id.nullable
    assert columns.price_uom_code.nullable
    assert columns.price_effective_from.nullable


def test_trade_line_price_provenance_constraints():
    names = _constraint_names()

    assert {
        "ck_trade_document_line_price_provenance_state",
        "ck_trade_document_line_price_type_code_nonempty",
        "ck_trade_document_line_price_uom_code_nonempty",
    } <= names


def test_trade_line_price_provenance_foreign_keys():
    constraints = [
        constraint
        for constraint
        in TradeDocumentLine.__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    ]

    price_type_fk = next(
        constraint
        for constraint in constraints
        if constraint.name
        == (
            "fk_trade_document_lines_"
            "company_price_type"
        )
    )

    assert [
        column.name
        for column in price_type_fk.columns
    ] == [
        "company_id",
        "price_type_code",
    ]

    assert [
        element.target_fullname
        for element in price_type_fk.elements
    ] == [
        "price_types.company_id",
        "price_types.code",
    ]

    product_price_fk = next(
        constraint
        for constraint in constraints
        if constraint.name
        == (
            "fk_trade_document_lines_"
            "company_source_product_price"
        )
    )

    assert [
        column.name
        for column in product_price_fk.columns
    ] == [
        "company_id",
        "source_product_price_id",
    ]

    assert [
        element.target_fullname
        for element in product_price_fk.elements
    ] == [
        "product_prices.company_id",
        "product_prices.id",
    ]


def test_manual_price_remains_supported():
    line = TradeDocumentLineCreate(
        product_id=1,
        quantity=Decimal("2.0000"),
        unit_price=Decimal("100.0000"),
    )

    assert line.unit_price == Decimal("100.0000")
    assert line.price_type_code is None


def test_master_price_request_without_manual_price():
    line = TradeDocumentLineCreate(
        product_id=1,
        quantity=Decimal("2.0000"),
        price_type_code=" retail ",
    )

    assert line.price_type_code == "RETAIL"
    assert line.unit_price == Decimal("0")
    assert "unit_price" not in line.model_fields_set


def test_manual_and_master_price_are_ambiguous():
    with pytest.raises(
        ValidationError,
        match=(
            "unit_price and price_type_code "
            "cannot be provided together"
        ),
    ):
        TradeDocumentLineCreate(
            product_id=1,
            quantity=Decimal("2.0000"),
            unit_price=Decimal("0"),
            price_type_code="RETAIL",
        )


def test_price_type_blank_rejected():
    with pytest.raises(ValidationError):
        TradeDocumentLineCreate(
            product_id=1,
            quantity=Decimal("1.0000"),
            price_type_code="   ",
        )


def test_response_provenance_effective_date_type():
    column = (
        TradeDocumentLine.__table__
        .c.price_effective_from
    )

    assert column.type.python_type is date
