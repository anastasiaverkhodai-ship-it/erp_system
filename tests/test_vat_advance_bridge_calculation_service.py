from datetime import date
from decimal import Decimal

import pytest

from app.services.vat_advance_bridge_calculation_service import (
    VatAdvanceBridgeDataIntegrityError,
    VatAdvanceBridgeTarget,
    build_vat_advance_bridge_target,
    calculate_vat_advance_bridge_amount,
)


D1 = date(
    2026,
    9,
    2,
)


def test_payment_first_requires_full_bridge():
    amount = (
        calculate_vat_advance_bridge_amount(
            sales_tax_amount=Decimal(
                "20.00"
            ),
            fulfillment_tax_amount=Decimal(
                "0.00"
            ),
            currency_code="UAH",
        )
    )

    assert amount == Decimal(
        "20.00"
    )


def test_fulfillment_first_requires_no_bridge():
    amount = (
        calculate_vat_advance_bridge_amount(
            sales_tax_amount=Decimal(
                "20.00"
            ),
            fulfillment_tax_amount=Decimal(
                "20.00"
            ),
            currency_code="UAH",
        )
    )

    assert amount == Decimal(
        "0.00"
    )


def test_partial_prepayment_requires_partial_bridge():
    amount = (
        calculate_vat_advance_bridge_amount(
            sales_tax_amount=Decimal(
                "20.00"
            ),
            fulfillment_tax_amount=Decimal(
                "10.00"
            ),
            currency_code="UAH",
        )
    )

    assert amount == Decimal(
        "10.00"
    )


def test_cash_method_fulfillment_keeps_full_bridge():
    amount = (
        calculate_vat_advance_bridge_amount(
            sales_tax_amount=Decimal(
                "20.00"
            ),
            fulfillment_tax_amount=Decimal(
                "0.00"
            ),
            currency_code="UAH",
        )
    )

    assert amount == Decimal(
        "20.00"
    )


def test_zero_rated_sales_requires_no_bridge():
    amount = (
        calculate_vat_advance_bridge_amount(
            sales_tax_amount=Decimal(
                "0.00"
            ),
            fulfillment_tax_amount=Decimal(
                "0.00"
            ),
            currency_code="UAH",
        )
    )

    assert amount == Decimal(
        "0.00"
    )


def test_fulfillment_vat_cannot_exceed_sales_vat():
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match=(
            "cannot exceed Sales recognition"
        ),
    ):
        calculate_vat_advance_bridge_amount(
            sales_tax_amount=Decimal(
                "10.00"
            ),
            fulfillment_tax_amount=Decimal(
                "10.01"
            ),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    (
        "sales_tax",
        "fulfillment_tax",
        "message",
    ),
    [
        (
            Decimal("-0.01"),
            Decimal("0.00"),
            "Sales recognition VAT",
        ),
        (
            Decimal("20.00"),
            Decimal("-0.01"),
            "Fulfillment-source VAT",
        ),
    ],
)
def test_negative_amounts_fail_closed(
    sales_tax,
    fulfillment_tax,
    message,
):
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match=message,
    ):
        calculate_vat_advance_bridge_amount(
            sales_tax_amount=sales_tax,
            fulfillment_tax_amount=(
                fulfillment_tax
            ),
            currency_code="UAH",
        )


def test_target_contains_complete_source_identity():
    target = (
        build_vat_advance_bridge_target(
            tax_calculation_id=11,
            source_id=22,
            event_date=D1,
            sales_tax_amount=Decimal(
                "20.00"
            ),
            fulfillment_tax_amount=Decimal(
                "5.00"
            ),
            currency_code="UAH",
        )
    )

    assert isinstance(
        target,
        VatAdvanceBridgeTarget,
    )

    assert target.tax_calculation_id == 11
    assert target.source_id == 22
    assert target.event_date == D1
    assert target.amount == Decimal(
        "15.00"
    )
    assert target.currency_code == "UAH"
    assert target.is_zero is False


def test_zero_target_is_explicit_state():
    target = (
        build_vat_advance_bridge_target(
            tax_calculation_id=11,
            source_id=22,
            event_date=D1,
            sales_tax_amount=Decimal(
                "20.00"
            ),
            fulfillment_tax_amount=Decimal(
                "20.00"
            ),
            currency_code="UAH",
        )
    )

    assert target.amount == Decimal(
        "0.00"
    )
    assert target.is_zero is True


@pytest.mark.parametrize(
    (
        "tax_calculation_id",
        "source_id",
        "message",
    ),
    [
        (
            0,
            22,
            "tax_calculation_id",
        ),
        (
            -1,
            22,
            "tax_calculation_id",
        ),
        (
            11,
            0,
            "source_id",
        ),
        (
            11,
            -1,
            "source_id",
        ),
    ],
)
def test_invalid_source_identity_fails_closed(
    tax_calculation_id,
    source_id,
    message,
):
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match=message,
    ):
        build_vat_advance_bridge_target(
            tax_calculation_id=(
                tax_calculation_id
            ),
            source_id=source_id,
            event_date=D1,
            sales_tax_amount=Decimal(
                "20.00"
            ),
            fulfillment_tax_amount=Decimal(
                "0.00"
            ),
            currency_code="UAH",
        )


def test_invalid_event_date_fails_closed():
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="event_date",
    ):
        build_vat_advance_bridge_target(
            tax_calculation_id=11,
            source_id=22,
            event_date="2026-09-02",
            sales_tax_amount=Decimal(
                "20.00"
            ),
            fulfillment_tax_amount=Decimal(
                "0.00"
            ),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    "currency_code",
    [
        "",
        "UA",
        "UAHH",
        None,
    ],
)
def test_invalid_currency_fails_closed(
    currency_code,
):
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="currency_code",
    ):
        calculate_vat_advance_bridge_amount(
            sales_tax_amount=Decimal(
                "20.00"
            ),
            fulfillment_tax_amount=Decimal(
                "0.00"
            ),
            currency_code=currency_code,
        )
