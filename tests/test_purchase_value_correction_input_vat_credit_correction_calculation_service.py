from datetime import date
from decimal import Decimal

import pytest

from app.services.purchase_value_correction_input_vat_credit_correction_calculation_service import (
    PurchaseValueCorrectionInputVatCreditCorrectionCalculationError,
    build_purchase_value_correction_input_vat_credit_target,
)


D5 = date(2026, 1, 5)


def build(**overrides):
    values = {
        "purchase_value_correction_vat_adjustment_event_id": 31,
        "tax_calculation_id": 21,
        "adjustment_date": D5,
        "adjustment_kind": "decrease",
        "adjusted_taxable_base": Decimal("10.00"),
        "adjusted_tax_amount": Decimal("2.00"),
        "recognized_credit_taxable_base": Decimal("100.00"),
        "recognized_credit_tax_amount": Decimal("20.00"),
        "currency_code": "UAH",
        "tax_credit_evidence_id": None,
        "tax_credit_evidence_type": None,
    }
    values.update(overrides)

    return build_purchase_value_correction_input_vat_credit_target(
        **values
    )


def test_decrease_without_rk_evidence():
    target = build()

    assert target is not None
    assert target.correction_kind == "decrease"
    assert target.tax_credit_evidence_id is None
    assert target.corrected_tax_amount == Decimal("2.00")


def test_decrease_is_capped_by_recognized_credit():
    target = build(
        adjusted_taxable_base=Decimal("150.00"),
        adjusted_tax_amount=Decimal("30.00"),
        recognized_credit_taxable_base=Decimal("40.00"),
        recognized_credit_tax_amount=Decimal("8.00"),
    )

    assert target is not None
    assert target.corrected_taxable_base == Decimal("40.00")
    assert target.corrected_tax_amount == Decimal("8.00")


def test_decrease_with_no_recognized_credit_is_noop():
    target = build(
        recognized_credit_taxable_base=Decimal("0.00"),
        recognized_credit_tax_amount=Decimal("0.00"),
    )

    assert target is None


def test_decrease_rejects_evidence_dependency():
    with pytest.raises(
        PurchaseValueCorrectionInputVatCreditCorrectionCalculationError
    ):
        build(
            tax_credit_evidence_id=77,
            tax_credit_evidence_type="registered_adjustment",
        )


def test_increase_requires_evidence():
    with pytest.raises(
        PurchaseValueCorrectionInputVatCreditCorrectionCalculationError
    ):
        build(
            adjustment_kind="increase",
        )


def test_increase_requires_registered_adjustment_type():
    with pytest.raises(
        PurchaseValueCorrectionInputVatCreditCorrectionCalculationError
    ):
        build(
            adjustment_kind="increase",
            tax_credit_evidence_id=77,
            tax_credit_evidence_type="registered_tax_invoice",
        )


def test_increase_with_registered_adjustment():
    target = build(
        adjustment_kind="increase",
        tax_credit_evidence_id=77,
        tax_credit_evidence_type="registered_adjustment",
    )

    assert target is not None
    assert target.correction_kind == "increase"
    assert target.tax_credit_evidence_id == 77
    assert target.corrected_taxable_base == Decimal("10.00")
    assert target.corrected_tax_amount == Decimal("2.00")
