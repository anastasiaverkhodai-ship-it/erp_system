from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1 import trade_documents
from app.services.invoice_tax_calculation_service import (
    calculate_invoice_line_tax,
)
from app.services.sales_credit_control_service import (
    calculate_sales_order_amount,
)
from app.services.sales_pricing_service import (
    ResolvedSalesPrice,
)
from app.services.tax_price_types import (
    TaxPriceMode,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
)


class _FakeDB:
    pass


def _line_data(
    *,
    unit_price=Decimal("0"),
    price_type_code=None,
    discount_percent=None,
    discount_amount_per_unit=None,
):
    return SimpleNamespace(
        product_id=1,
        unit_price=unit_price,
        price_type_code=price_type_code,
        discount_percent=discount_percent,
        discount_amount_per_unit=(
            discount_amount_per_unit
        ),
    )


@pytest.mark.asyncio
async def test_manual_percent_discount_snapshot():
    result = await (
        trade_documents
        ._resolve_trade_line_price_snapshot(
            _FakeDB(),
            company_id=1,
            line_data=_line_data(
                unit_price=Decimal("100"),
                discount_percent=Decimal("10"),
            ),
            direction=TradeDirection.SALE,
            document_date=date(
                2026,
                9,
                14,
            ),
            currency_code="UAH",
        )
    )

    assert (
        result["unit_price"]
        == Decimal("90.0000")
    )
    assert (
        result["base_unit_price"]
        == Decimal("100.0000")
    )
    assert (
        result["discount_percent"]
        == Decimal("10")
    )
    assert (
        result["discount_amount_per_unit"]
        is None
    )

    assert result["price_type_code"] is None
    assert (
        result["source_product_price_id"]
        is None
    )


@pytest.mark.asyncio
async def test_manual_amount_discount_snapshot():
    result = await (
        trade_documents
        ._resolve_trade_line_price_snapshot(
            _FakeDB(),
            company_id=1,
            line_data=_line_data(
                unit_price=Decimal("100"),
                discount_amount_per_unit=(
                    Decimal("7.2500")
                ),
            ),
            direction=TradeDirection.SALE,
            document_date=date(
                2026,
                9,
                14,
            ),
            currency_code="UAH",
        )
    )

    assert (
        result["unit_price"]
        == Decimal("92.7500")
    )
    assert (
        result["base_unit_price"]
        == Decimal("100.0000")
    )
    assert (
        result["discount_amount_per_unit"]
        == Decimal("7.2500")
    )


@pytest.mark.asyncio
async def test_master_percent_discount_preserves_master_provenance(
    monkeypatch,
):
    async def fake_resolve(
        db,
        *,
        company_id,
        product_id,
        price_type_code,
        document_date,
        document_currency_code,
        document_direction,
    ):
        return ResolvedSalesPrice(
            unit_price=Decimal("125.5000"),
            price_type_code="RETAIL",
            source_product_price_id=77,
            price_uom_code="pcs",
            price_effective_from=date(
                2026,
                9,
                1,
            ),
        )

    monkeypatch.setattr(
        trade_documents,
        "resolve_sales_master_price",
        fake_resolve,
    )

    result = await (
        trade_documents
        ._resolve_trade_line_price_snapshot(
            _FakeDB(),
            company_id=1,
            line_data=_line_data(
                price_type_code="RETAIL",
                discount_percent=Decimal("20"),
            ),
            direction=TradeDirection.SALE,
            document_date=date(
                2026,
                9,
                14,
            ),
            currency_code="UAH",
        )
    )

    assert (
        result["base_unit_price"]
        == Decimal("125.5000")
    )
    assert (
        result["unit_price"]
        == Decimal("100.4000")
    )

    assert (
        result["price_type_code"]
        == "RETAIL"
    )
    assert (
        result["source_product_price_id"]
        == 77
    )
    assert (
        result["price_uom_code"]
        == "pcs"
    )
    assert (
        result["price_effective_from"]
        == date(
            2026,
            9,
            1,
        )
    )


@pytest.mark.asyncio
async def test_invalid_amount_discount_is_http_422():
    with pytest.raises(
        HTTPException
    ) as exc_info:
        await (
            trade_documents
            ._resolve_trade_line_price_snapshot(
                _FakeDB(),
                company_id=1,
                line_data=_line_data(
                    unit_price=Decimal("100"),
                    discount_amount_per_unit=(
                        Decimal("101")
                    ),
                ),
                direction=TradeDirection.SALE,
                document_date=date(
                    2026,
                    9,
                    14,
                ),
                currency_code="UAH",
            )
        )

    assert (
        exc_info.value.status_code
        == 422
    )


def test_vat_uses_final_discounted_unit_price():
    document = SimpleNamespace(
        document_date=date(
            2026,
            9,
            14,
        ),
        currency_code="UAH",
    )

    line = SimpleNamespace(
        quantity=Decimal("2"),
        unit_price=Decimal("90.0000"),
        tax_rate_code="VAT20",
        tax_recognition_method=(
            TaxRecognitionMethod.FIRST_EVENT
        ),
        tax_price_mode=(
            TaxPriceMode.EXCLUSIVE
        ),
    )

    result = calculate_invoice_line_tax(
        document=document,
        line=line,
    )

    assert result is not None
    assert (
        result.taxable_base
        == Decimal("180.00")
    )
    assert (
        result.tax_amount
        == Decimal("36.00")
    )
    assert (
        result.gross_amount
        == Decimal("216.00")
    )


def test_sales_credit_uses_final_discounted_unit_price():
    line = SimpleNamespace(
        quantity=Decimal("2"),
        unit_price=Decimal("90.0000"),
        tax_rate_code=None,
        tax_recognition_method=None,
        tax_price_mode=None,
    )

    document = SimpleNamespace(
        direction=TradeDirection.SALE,
        kind=TradeDocumentKind.ORDER,
        currency_code="UAH",
        document_date=date(
            2026,
            9,
            14,
        ),
        lines=[
            line,
        ],
    )

    assert (
        calculate_sales_order_amount(
            document
        )
        == Decimal("180.00")
    )
