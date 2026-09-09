from datetime import date
from decimal import Decimal

import pytest

from app.services.purchase_value_correction_supplier_vat_liability_calculation_service import (
    PurchaseValueCorrectionSupplierVatLiabilityAmountError,
    PurchaseValueCorrectionSupplierVatLiabilitySourceError,
    PurchaseValueCorrectionSupplierVatAdjustment,
    SupplierVatAllocationCandidate,
    build_purchase_value_correction_supplier_vat_adjustments,
)


D1 = date(
    2026,
    9,
    1,
)


def candidate(
    source_id,
    amount,
):
    return SupplierVatAllocationCandidate(
        source_id=source_id,
        event_date=D1,
        amount=Decimal(amount),
    )


def test_decrease_one_source():
    result = (
        build_purchase_value_correction_supplier_vat_adjustments(
            components=(
                candidate(
                    10,
                    "20.00",
                ),
            ),
            adjustment_kind="decrease",
            adjusted_tax_amount=Decimal(
                "2.00"
            ),
        )
    )

    assert result == (
        PurchaseValueCorrectionSupplierVatAdjustment(
            source_id=10,
            event_date=D1,
            amount_delta=Decimal(
                "-2.00"
            ),
        ),
    )


def test_increase_one_source():
    result = (
        build_purchase_value_correction_supplier_vat_adjustments(
            components=(
                candidate(
                    10,
                    "20.00",
                ),
            ),
            adjustment_kind="increase",
            adjusted_tax_amount=Decimal(
                "2.00"
            ),
        )
    )

    assert result[0].amount_delta == Decimal(
        "2.00"
    )


def test_two_source_proportional_decrease():
    result = (
        build_purchase_value_correction_supplier_vat_adjustments(
            components=(
                candidate(
                    10,
                    "12.00",
                ),
                candidate(
                    20,
                    "8.00",
                ),
            ),
            adjustment_kind="decrease",
            adjusted_tax_amount=Decimal(
                "2.00"
            ),
        )
    )

    assert tuple(
        item.amount_delta
        for item in result
    ) == (
        Decimal("-1.20"),
        Decimal("-0.80"),
    )


def test_cumulative_rounding_conserves_exact_total():
    result = (
        build_purchase_value_correction_supplier_vat_adjustments(
            components=(
                candidate(
                    1,
                    "0.01",
                ),
                candidate(
                    2,
                    "0.01",
                ),
                candidate(
                    3,
                    "0.01",
                ),
            ),
            adjustment_kind="increase",
            adjusted_tax_amount=Decimal(
                "0.02"
            ),
        )
    )

    assert sum(
        (
            item.amount_delta
            for item in result
        ),
        Decimal("0"),
    ) == Decimal(
        "0.02"
    )


def test_final_source_closes_exactly():
    result = (
        build_purchase_value_correction_supplier_vat_adjustments(
            components=(
                candidate(
                    1,
                    "33.33",
                ),
                candidate(
                    2,
                    "33.33",
                ),
                candidate(
                    3,
                    "33.34",
                ),
            ),
            adjustment_kind="decrease",
            adjusted_tax_amount=Decimal(
                "10.01"
            ),
        )
    )

    assert sum(
        (
            item.amount_delta
            for item in result
        ),
        Decimal("0"),
    ) == Decimal(
        "-10.01"
    )


def test_zero_tax_is_noop():
    assert (
        build_purchase_value_correction_supplier_vat_adjustments(
            components=(),
            adjustment_kind="decrease",
            adjusted_tax_amount=Decimal(
                "0.00"
            ),
        )
        == ()
    )


def test_decrease_cannot_exceed_current_vat():
    with pytest.raises(
        PurchaseValueCorrectionSupplierVatLiabilityAmountError
    ):
        build_purchase_value_correction_supplier_vat_adjustments(
            components=(
                candidate(
                    1,
                    "5.00",
                ),
            ),
            adjustment_kind="decrease",
            adjusted_tax_amount=Decimal(
                "5.01"
            ),
        )


def test_nonzero_requires_current_component_provenance():
    with pytest.raises(
        PurchaseValueCorrectionSupplierVatLiabilitySourceError
    ):
        build_purchase_value_correction_supplier_vat_adjustments(
            components=(),
            adjustment_kind="increase",
            adjusted_tax_amount=Decimal(
                "1.00"
            ),
        )


def test_duplicate_source_rejected():
    with pytest.raises(
        PurchaseValueCorrectionSupplierVatLiabilitySourceError
    ):
        build_purchase_value_correction_supplier_vat_adjustments(
            components=(
                candidate(
                    1,
                    "10.00",
                ),
                candidate(
                    1,
                    "10.00",
                ),
            ),
            adjustment_kind="decrease",
            adjusted_tax_amount=Decimal(
                "1.00"
            ),
        )


def test_invalid_direction_rejected():
    with pytest.raises(
        PurchaseValueCorrectionSupplierVatLiabilitySourceError
    ):
        build_purchase_value_correction_supplier_vat_adjustments(
            components=(
                candidate(
                    1,
                    "10.00",
                ),
            ),
            adjustment_kind="other",
            adjusted_tax_amount=Decimal(
                "1.00"
            ),
        )
