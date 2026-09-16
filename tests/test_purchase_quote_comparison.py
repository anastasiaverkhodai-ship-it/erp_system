from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from app.schemas.purchase_quote import PurchaseQuoteCreate, PurchaseQuoteComparisonRequest
from app.services.purchase_quote_service import rank_purchase_quotes
from app.services.purchase_policy_service import purchase_payment_terms, validate_purchase_supplier, PurchasePolicyError
from app.api.v1 import trade_documents as api
from app.schemas.trade_document import TradeDocumentLineCreate
from app.services.trade_document_types import TradeDirection

DAY = date(2026, 9, 15)


def quote(id=1, **changes):
    values = dict(id=id, company_id=1, supplier_id=id, product_id=1, contract_id=None, reference=f"Q-{id}",
        currency_code="UAH", min_quantity=D("1"), max_quantity=D("100"), unit_price_net=D("10"),
        unit_price_gross=D("12"), delivery_net=D("0"), delivery_gross=D("0"), lead_time_days=2,
        payment_term_days=30, valid_from=DAY, valid_until=DAY+timedelta(days=30), is_active=True)
    return SimpleNamespace(**(values | changes))


def compare(offers, **changes):
    request = PurchaseQuoteComparisonRequest(**(dict(product_id=1, quantity=D("10"), order_date=DAY) | changes))
    suppliers = {q.supplier_id: SimpleNamespace(id=q.supplier_id, company_id=1, name=f"Supplier {q.supplier_id}",
        is_active=True, counterparty_type="supplier") for q in offers}
    return rank_purchase_quotes(company_id=1, request=request, quotes=offers, suppliers=suppliers, contracts={})


def test_ranking_uses_total_payable_including_delivery_and_vat():
    # Lower net price loses when its VAT-inclusive delivered amount is higher.
    result = compare([quote(1, unit_price_net=D("8"), unit_price_gross=D("12")),
                      quote(2, unit_price_net=D("9"), unit_price_gross=D("9"), delivery_net=D("20"), delivery_gross=D("20"))])
    assert result.recommended_quote_id == 2
    assert [r.total_payable for r in result.ranked] == [D("110"), D("120")]
    assert result.ranked[1].total_vat == D("40")


def test_ties_prefer_earlier_delivery_then_stable_quote_id():
    assert [r.quote_id for r in compare([quote(3), quote(2, lead_time_days=1), quote(1)]).ranked] == [2, 1, 3]


@pytest.mark.parametrize("change,reason", [
    ({"is_active": False}, "quote_withdrawn"),
    ({"valid_until": DAY-timedelta(days=1)}, "quote_not_valid_on_order_date"),
    ({"valid_from": DAY+timedelta(days=1)}, "quote_not_valid_on_order_date"),
    ({"min_quantity": D("11")}, "quantity_outside_offer_range"),
    ({"max_quantity": D("9")}, "quantity_outside_offer_range"),
    ({"currency_code": "EUR"}, "currency_mismatch"),
    ({"lead_time_days": 5}, "delivery_after_deadline"),
])
def test_ineligible_offers_have_explanation(change, reason):
    result = compare([quote(**change)], required_delivery_date=DAY+timedelta(days=2))
    assert result.recommended_quote_id is None
    assert reason in result.rejected[0].reasons


def test_foreign_company_quotes_are_not_disclosed():
    result = compare([quote(company_id=2)])
    assert not result.ranked and not result.rejected


def test_dates_and_quantity_boundaries_are_inclusive():
    assert compare([quote(min_quantity=D("10"), max_quantity=D("10"), valid_until=DAY)],
                   required_delivery_date=DAY+timedelta(days=2)).recommended_quote_id == 1


def test_totals_round_only_extended_line_and_include_one_delivery_charge():
    result = compare([quote(unit_price_net=D("0.3333"), unit_price_gross=D("0.4000"),
                            delivery_net=D("1"), delivery_gross=D("1.20"))], quantity=D("3"))
    assert result.ranked[0].goods_net == D("1.00")
    assert result.ranked[0].total_payable == D("2.40")


@pytest.mark.parametrize("explicit,expected", [(None, 30), (0, 0), (15, 15)])
def test_purchase_terms_explicit_value_wins_including_zero(explicit, expected):
    assert purchase_payment_terms(counterparty=SimpleNamespace(payment_term_days=7),
        contract=SimpleNamespace(payment_term_days=30), explicit_days=explicit) == expected


def test_purchase_terms_fall_back_to_supplier():
    assert purchase_payment_terms(counterparty=SimpleNamespace(payment_term_days=7), contract=None, explicit_days=None) == 7


def test_customer_only_cannot_be_purchase_supplier():
    with pytest.raises(PurchasePolicyError): validate_purchase_supplier(SimpleNamespace(counterparty_type="customer"))


@pytest.mark.asyncio
async def test_purchase_price_does_not_inherit_sales_defaults(monkeypatch):
    resolver = AsyncMock(side_effect=AssertionError("sales policy must not run"))
    monkeypatch.setattr(api, "resolve_sales_commercial_policy", resolver)
    line = TradeDocumentLineCreate(product_id=1, warehouse_id=1, quantity=D("2"), unit_price=D("10"), discount_percent=D("10"))
    result = await api._resolve_trade_line_price_snapshot(None, company_id=1, counterparty_id=1, contract_id=1,
        line_data=line, direction=TradeDirection.PURCHASE, document_date=DAY, currency_code="UAH")
    assert result["unit_price"] == D("9")
    assert result["source_product_price_id"] is None
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_purchase_rejects_explicit_sales_price_type():
    with pytest.raises(HTTPException) as failure:
        await api._resolve_trade_line_price_snapshot(None, company_id=1, counterparty_id=1, contract_id=None,
            line_data=TradeDocumentLineCreate(product_id=1, quantity=D("1"), price_type_code="RETAIL"),
            direction=TradeDirection.PURCHASE, document_date=DAY, currency_code="UAH")
    assert failure.value.status_code == 422


@pytest.mark.parametrize("changes", [{"unit_price_net":"NaN"}, {"unit_price_gross":"9"},
    {"delivery_net":"2","delivery_gross":"1"}, {"valid_until":DAY-timedelta(days=1)},
    {"reference":" "}, {"min_quantity":"0"}, {"max_quantity":"0.5"}])
def test_invalid_quote_terms_rejected(changes):
    values = vars(quote()).copy()
    for key in ("id", "company_id", "is_active"): values.pop(key)
    with pytest.raises(ValidationError): PurchaseQuoteCreate(**(values | changes))
