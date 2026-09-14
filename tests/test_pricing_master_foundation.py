from datetime import date
from decimal import Decimal

import pytest

from app.models.price_type import PriceType
from app.models.product_price import ProductPrice
from app.services.price_types import PriceKind
from app.services.pricing_master_service import (
    PricingMasterError,
    _normalize_code,
    _normalize_currency,
    _normalize_name,
    _normalize_uom,
)


def test_price_type_table_identity():
    assert PriceType.__tablename__ == "price_types"

    constraints = {
        constraint.name
        for constraint in PriceType.__table__.constraints
        if constraint.name
    }

    assert "uq_price_types_company_code" in constraints
    assert "uq_price_types_company_id_id" in constraints


def test_product_price_table_identity():
    assert ProductPrice.__tablename__ == "product_prices"

    constraints = {
        constraint.name
        for constraint in ProductPrice.__table__.constraints
        if constraint.name
    }

    assert (
        "uq_product_prices_effective_identity"
        in constraints
    )
    assert "uq_product_prices_company_id_id" in constraints


def test_price_master_normalization():
    assert _normalize_code(" retail ") == "RETAIL"
    assert _normalize_name(" Retail ") == "Retail"
    assert _normalize_currency(" uah ") == "UAH"
    assert _normalize_uom(" pcs ") == "PCS"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "  ",
    ],
)
def test_blank_price_type_code_fails_closed(value):
    with pytest.raises(PricingMasterError):
        _normalize_code(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "UA",
        "UAHH",
        "12A",
    ],
)
def test_invalid_currency_fails_closed(value):
    with pytest.raises(PricingMasterError):
        _normalize_currency(value)


def test_product_price_amount_precision_is_four_decimals():
    column = ProductPrice.__table__.c.amount

    assert column.type.precision == 18
    assert column.type.scale == 4


def test_foundation_values_match_existing_domain_semantics():
    price_type = PriceType(
        company_id=1,
        code="RETAIL",
        name="Retail",
        kind=PriceKind.SALES.value,
        currency_code="UAH",
    )

    product_price = ProductPrice(
        company_id=1,
        product_id=10,
        price_type_code="RETAIL",
        amount=Decimal("125.5000"),
        uom_code="PCS",
        effective_from=date(2026, 1, 1),
    )

    assert price_type.kind == "sales"
    assert product_price.amount == Decimal("125.5000")
    assert product_price.effective_from == date(2026, 1, 1)
