from datetime import date
from decimal import Decimal

import pytest

from app.services.purchase_value_correction_vat_adjustment_calculation_service import (
    PurchaseValueCorrectionVatAdjustmentCalculationError,
    build_purchase_value_correction_vat_adjustment_target,
)


D5 = date(2026, 1, 5)


def build(**overrides):
    values = {
        "trade_value_correction_event_id": 11,
        "tax_calculation_id": 21,
        "adjustment_date": D5,
        "original_taxable_base": Decimal("100.00"),
        "corrected_taxable_base": Decimal("90.00"),
        "original_tax_amount": Decimal("20.00"),
        "corrected_tax_amount": Decimal("18.00"),
        "currency_code": "UAH",
    }
    values.update(overrides)
    return build_purchase_value_correction_vat_adjustment_target(
        **values
    )


def test_decrease():
    target = build()

    assert target is not None
    assert target.adjustment_kind == "decrease"
    assert target.adjusted_taxable_base == Decimal("10.00")
    assert target.adjusted_tax_amount == Decimal("2.00")


def test_increase():
    target = build(
        corrected_taxable_base=Decimal("110.00"),
        corrected_tax_amount=Decimal("22.00"),
    )

    assert target is not None
    assert target.adjustment_kind == "increase"
    assert target.adjusted_taxable_base == Decimal("10.00")
    assert target.adjusted_tax_amount == Decimal("2.00")


def test_tax_only_change():
    target = build(
        corrected_taxable_base=Decimal("100.00"),
        corrected_tax_amount=Decimal("18.00"),
    )

    assert target is not None
    assert target.adjustment_kind == "decrease"
    assert target.adjusted_taxable_base == Decimal("0.00")
    assert target.adjusted_tax_amount == Decimal("2.00")


def test_noop():
    target = build(
        corrected_taxable_base=Decimal("100.00"),
        corrected_tax_amount=Decimal("20.00"),
    )

    assert target is None


def test_mixed_direction_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionVatAdjustmentCalculationError
    ):
        build(
            corrected_taxable_base=Decimal("90.00"),
            corrected_tax_amount=Decimal("22.00"),
        )


def test_rounding():
    target = build(
        corrected_taxable_base=Decimal("90.004"),
        corrected_tax_amount=Decimal("18.004"),
    )

    assert target is not None
    assert target.adjusted_taxable_base == Decimal("10.00")
    assert target.adjusted_tax_amount == Decimal("2.00")
