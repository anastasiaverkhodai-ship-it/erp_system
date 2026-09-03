from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.purchase_receipt_accounting_price_service import (
    PurchaseReceiptAccountingAllocationError,
    PurchaseReceiptAccountingSourceError,
    calculate_purchase_receipt_accounting_slice,
)
from app.services.tax_price_types import TaxPriceMode
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
)


D1 = date(
    2026,
    8,
    10,
)


def document():
    return SimpleNamespace(
        direction=TradeDirection.PURCHASE,
        kind=TradeDocumentKind.ORDER,
        document_date=D1,
        currency_code="UAH",
    )


def line(
    *,
    quantity="1",
    unit_price="100",
    tax_rate_code=None,
    tax_price_mode=None,
    tax_recognition_method=None,
):
    return SimpleNamespace(
        quantity=Decimal(
            quantity
        ),
        unit_price=Decimal(
            unit_price
        ),
        tax_rate_code=tax_rate_code,
        tax_price_mode=tax_price_mode,
        tax_recognition_method=(
            tax_recognition_method
        ),
    )


def vat20_line(
    *,
    quantity="1",
    unit_price="100",
    price_mode=TaxPriceMode.EXCLUSIVE,
):
    return line(
        quantity=quantity,
        unit_price=unit_price,
        tax_rate_code="VAT20",
        tax_price_mode=price_mode,
        tax_recognition_method=(
            TaxRecognitionMethod.FIRST_EVENT
        ),
    )


def test_unconfigured_purchase_price_is_unchanged():
    result = (
        calculate_purchase_receipt_accounting_slice(
            document=document(),
            line=line(
                quantity="2",
                unit_price="125.50",
            ),
            fulfilled_before=Decimal("0"),
            fulfilled_after=Decimal("2"),
        )
    )

    assert result.amount == Decimal(
        "251.00"
    )
    assert result.unit_price == Decimal(
        "125.5000"
    )


def test_vat20_exclusive_purchase_price_is_unchanged():
    result = (
        calculate_purchase_receipt_accounting_slice(
            document=document(),
            line=vat20_line(
                unit_price="100",
                price_mode=(
                    TaxPriceMode.EXCLUSIVE
                ),
            ),
            fulfilled_before=Decimal("0"),
            fulfilled_after=Decimal("1"),
        )
    )

    assert result.amount == Decimal(
        "100.00"
    )
    assert result.unit_price == Decimal(
        "100.0000"
    )


def test_vat20_inclusive_120_becomes_base_100():
    result = (
        calculate_purchase_receipt_accounting_slice(
            document=document(),
            line=vat20_line(
                unit_price="120",
                price_mode=(
                    TaxPriceMode.INCLUSIVE
                ),
            ),
            fulfilled_before=Decimal("0"),
            fulfilled_after=Decimal("1"),
        )
    )

    assert result.amount == Decimal(
        "100.00"
    )
    assert result.unit_price == Decimal(
        "100.0000"
    )


def test_inclusive_partial_receipts_preserve_full_base():
    source = vat20_line(
        quantity="3",
        unit_price="100",
        price_mode=TaxPriceMode.INCLUSIVE,
    )

    first = calculate_purchase_receipt_accounting_slice(
        document=document(),
        line=source,
        fulfilled_before=Decimal("0"),
        fulfilled_after=Decimal("1"),
    )

    second = calculate_purchase_receipt_accounting_slice(
        document=document(),
        line=source,
        fulfilled_before=Decimal("1"),
        fulfilled_after=Decimal("2"),
    )

    third = calculate_purchase_receipt_accounting_slice(
        document=document(),
        line=source,
        fulfilled_before=Decimal("2"),
        fulfilled_after=Decimal("3"),
    )

    assert (
        first.amount,
        second.amount,
        third.amount,
    ) == (
        Decimal("83.33"),
        Decimal("83.34"),
        Decimal("83.33"),
    )

    assert (
        first.amount
        + second.amount
        + third.amount
    ) == Decimal(
        "250.00"
    )

    assert first.unit_price == Decimal(
        "83.3300"
    )
    assert second.unit_price == Decimal(
        "83.3400"
    )
    assert third.unit_price == Decimal(
        "83.3300"
    )


def test_partial_receipt_uses_cumulative_delta_not_naive_rounding():
    source = vat20_line(
        quantity="3",
        unit_price="100",
        price_mode=TaxPriceMode.INCLUSIVE,
    )

    middle = calculate_purchase_receipt_accounting_slice(
        document=document(),
        line=source,
        fulfilled_before=Decimal("1"),
        fulfilled_after=Decimal("2"),
    )

    assert middle.amount == Decimal(
        "83.34"
    )


def test_wrong_trade_direction_is_rejected():
    source_document = document()

    source_document.direction = (
        TradeDirection.SALE
    )

    with pytest.raises(
        PurchaseReceiptAccountingSourceError,
        match="PURCHASE",
    ):
        calculate_purchase_receipt_accounting_slice(
            document=source_document,
            line=line(),
            fulfilled_before=Decimal("0"),
            fulfilled_after=Decimal("1"),
        )


def test_invalid_fulfillment_interval_is_rejected():
    with pytest.raises(
        PurchaseReceiptAccountingAllocationError,
        match="greater than",
    ):
        calculate_purchase_receipt_accounting_slice(
            document=document(),
            line=line(),
            fulfilled_before=Decimal("1"),
            fulfilled_after=Decimal("1"),
        )


def test_partial_tax_configuration_is_rejected():
    source = line(
        tax_rate_code="VAT20",
        tax_price_mode=None,
        tax_recognition_method=(
            TaxRecognitionMethod.FIRST_EVENT
        ),
    )

    with pytest.raises(
        PurchaseReceiptAccountingSourceError,
        match="VAT configuration",
    ):
        calculate_purchase_receipt_accounting_slice(
            document=document(),
            line=source,
            fulfilled_before=Decimal("0"),
            fulfilled_after=Decimal("1"),
        )
