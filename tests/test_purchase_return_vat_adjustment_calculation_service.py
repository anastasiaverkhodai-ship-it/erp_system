from datetime import date
from decimal import Decimal

import pytest

from app.services.purchase_return_vat_adjustment_calculation_service import (
    PurchaseReturnVatAdjustmentDataIntegrityError,
    build_purchase_return_vat_adjustment_target,
)


D = date(
    2026,
    9,
    5,
)


def build(
    **overrides,
):
    values = {
        "purchase_return_recognition_event_id":
            10,
        "tax_calculation_id":
            20,
        "adjustment_date":
            D,
        "basis_kind":
            "goods_received_by_supplier",
        "adjusted_taxable_base":
            Decimal("100.00"),
        "adjusted_tax_amount":
            Decimal("20.00"),
        "currency_code":
            "UAH",
    }

    values.update(
        overrides
    )

    return (
        build_purchase_return_vat_adjustment_target(
            **values
        )
    )


def test_positive_target():
    target = build()

    assert (
        target.purchase_return_recognition_event_id
        == 10
    )
    assert target.tax_calculation_id == 20
    assert target.adjustment_date == D
    assert (
        target.basis_kind
        == "goods_received_by_supplier"
    )
    assert (
        target.adjusted_taxable_base
        == Decimal("100.00")
    )
    assert (
        target.adjusted_tax_amount
        == Decimal("20.00")
    )
    assert target.currency_code == "UAH"
    assert target.is_zero is False


def test_refund_basis_is_supported():
    target = build(
        basis_kind="refund_by_supplier"
    )

    assert (
        target.basis_kind
        == "refund_by_supplier"
    )


def test_zero_target_is_valid_reconciliation_state():
    target = build(
        adjusted_taxable_base=Decimal("0.00"),
        adjusted_tax_amount=Decimal("0.00"),
    )

    assert target.is_zero is True


@pytest.mark.parametrize(
    "field",
    (
        "purchase_return_recognition_event_id",
        "tax_calculation_id",
    ),
)
def test_source_ids_must_be_positive(
    field,
):
    with pytest.raises(
        PurchaseReturnVatAdjustmentDataIntegrityError
    ):
        build(
            **{
                field: 0,
            }
        )


def test_unknown_basis_fails_closed():
    with pytest.raises(
        PurchaseReturnVatAdjustmentDataIntegrityError
    ):
        build(
            basis_kind="warehouse_issue"
        )


@pytest.mark.parametrize(
    "field",
    (
        "adjusted_taxable_base",
        "adjusted_tax_amount",
    ),
)
def test_negative_amount_fails(
    field,
):
    with pytest.raises(
        PurchaseReturnVatAdjustmentDataIntegrityError
    ):
        build(
            **{
                field:
                    Decimal("-0.01"),
            }
        )


@pytest.mark.parametrize(
    "value",
    (
        "NaN",
        "Infinity",
        "-Infinity",
    ),
)
def test_nonfinite_amount_fails(
    value,
):
    with pytest.raises(
        PurchaseReturnVatAdjustmentDataIntegrityError
    ):
        build(
            adjusted_tax_amount=Decimal(
                value
            )
        )


def test_currency_must_be_three_characters():
    with pytest.raises(
        PurchaseReturnVatAdjustmentDataIntegrityError
    ):
        build(
            currency_code="UA"
        )


def test_base_and_tax_are_independent():
    target = build(
        adjusted_taxable_base=Decimal("0.00"),
        adjusted_tax_amount=Decimal("0.01"),
    )

    assert (
        target.adjusted_taxable_base
        == Decimal("0.00")
    )
    assert (
        target.adjusted_tax_amount
        == Decimal("0.01")
    )
    assert target.is_zero is False
