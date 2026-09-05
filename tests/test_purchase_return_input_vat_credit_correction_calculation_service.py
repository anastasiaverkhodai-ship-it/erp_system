from datetime import date
from decimal import Decimal

import pytest

from app.services.purchase_return_input_vat_credit_correction_calculation_service import (
    PurchaseReturnInputVatCreditCorrectionCalculationError,
    build_purchase_return_input_vat_credit_correction_target,
)


D1 = date(
    2026,
    9,
    5,
)


def build(
    *,
    calc_base="100.00",
    calc_tax="20.00",
    formed_base="100.00",
    formed_tax="20.00",
    prior_base="0.00",
    prior_tax="0.00",
    current_base="25.00",
    current_tax="5.00",
):
    return (
        build_purchase_return_input_vat_credit_correction_target(
            purchase_return_vat_adjustment_event_id=10,
            tax_calculation_id=20,
            adjustment_date=D1,
            calculation_taxable_base=Decimal(
                calc_base
            ),
            calculation_tax_amount=Decimal(
                calc_tax
            ),
            formed_credit_taxable_base=Decimal(
                formed_base
            ),
            formed_credit_tax_amount=Decimal(
                formed_tax
            ),
            prior_active_return_taxable_base=Decimal(
                prior_base
            ),
            prior_active_return_tax_amount=Decimal(
                prior_tax
            ),
            current_return_taxable_base=Decimal(
                current_base
            ),
            current_return_tax_amount=Decimal(
                current_tax
            ),
            currency_code="UAH",
        )
    )


def test_full_credit_reduces_full_current_return_slice():
    target = build()

    assert (
        target.reduced_taxable_base
        == Decimal("25.00")
    )

    assert (
        target.reduced_tax_amount
        == Decimal("5.00")
    )


def test_partial_credit_below_remaining_capacity_needs_no_correction():
    target = build(
        formed_base="50.00",
        formed_tax="10.00",
    )

    assert target.is_zero is True


def test_partial_credit_above_remaining_capacity_reduces_only_excess():
    target = build(
        formed_base="90.00",
        formed_tax="18.00",
    )

    assert (
        target.reduced_taxable_base
        == Decimal("15.00")
    )

    assert (
        target.reduced_tax_amount
        == Decimal("3.00")
    )


def test_no_credit_formed_needs_no_correction():
    target = build(
        formed_base="0.00",
        formed_tax="0.00",
    )

    assert target.is_zero is True


def test_prior_return_capacity_is_respected():
    target = build(
        formed_base="75.00",
        formed_tax="15.00",
        prior_base="25.00",
        prior_tax="5.00",
        current_base="25.00",
        current_tax="5.00",
    )

    assert (
        target.reduced_taxable_base
        == Decimal("25.00")
    )

    assert (
        target.reduced_tax_amount
        == Decimal("5.00")
    )


def test_base_and_tax_are_independent():
    target = build(
        formed_base="100.00",
        formed_tax="10.00",
    )

    assert (
        target.reduced_taxable_base
        == Decimal("25.00")
    )

    assert (
        target.reduced_tax_amount
        == Decimal("0.00")
    )


def test_formed_credit_cannot_exceed_calculation():
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionCalculationError
    ):
        build(
            formed_tax="20.01"
        )


def test_cumulative_return_cannot_exceed_calculation():
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionCalculationError
    ):
        build(
            prior_tax="18.00",
            current_tax="5.00",
        )


@pytest.mark.parametrize(
    "field,value",
    (
        (
            "purchase_return_vat_adjustment_event_id",
            0,
        ),
        (
            "tax_calculation_id",
            0,
        ),
    ),
)
def test_invalid_ids_fail(
    field,
    value,
):
    kwargs = {
        "purchase_return_vat_adjustment_event_id": 10,
        "tax_calculation_id": 20,
        "adjustment_date": D1,
        "calculation_taxable_base": Decimal("100"),
        "calculation_tax_amount": Decimal("20"),
        "formed_credit_taxable_base": Decimal("100"),
        "formed_credit_tax_amount": Decimal("20"),
        "prior_active_return_taxable_base": Decimal("0"),
        "prior_active_return_tax_amount": Decimal("0"),
        "current_return_taxable_base": Decimal("25"),
        "current_return_tax_amount": Decimal("5"),
        "currency_code": "UAH",
    }

    kwargs[
        field
    ] = value

    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionCalculationError
    ):
        build_purchase_return_input_vat_credit_correction_target(
            **kwargs
        )


def test_zero_current_return_fails():
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionCalculationError
    ):
        build(
            current_base="0.00",
            current_tax="0.00",
        )


def test_invalid_currency_fails():
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionCalculationError
    ):
        build_purchase_return_input_vat_credit_correction_target(
            purchase_return_vat_adjustment_event_id=10,
            tax_calculation_id=20,
            adjustment_date=D1,
            calculation_taxable_base=Decimal("100"),
            calculation_tax_amount=Decimal("20"),
            formed_credit_taxable_base=Decimal("100"),
            formed_credit_tax_amount=Decimal("20"),
            prior_active_return_taxable_base=Decimal("0"),
            prior_active_return_tax_amount=Decimal("0"),
            current_return_taxable_base=Decimal("25"),
            current_return_tax_amount=Decimal("5"),
            currency_code="UA",
        )
