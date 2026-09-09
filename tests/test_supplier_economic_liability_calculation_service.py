from datetime import date
from decimal import Decimal

import pytest

from app.services.supplier_economic_liability_calculation_service import (
    SupplierEconomicLiabilityAmountError,
    SupplierEconomicLiabilityCurrencyError,
    SupplierEconomicLiabilitySourceError,
    SupplierReceiptBaseAllocationCandidate,
    SupplierReceiptBaseAllocationTarget,
    SupplierVatLiabilityComponent,
    build_supplier_economic_liability_candidates,
    build_supplier_receipt_base_allocation_targets,
)


D1 = date(
    2026,
    8,
    10,
)

D2 = date(
    2026,
    8,
    20,
)


def allocation(
    source_id: int,
    quantity: str,
    *,
    event_date=D1,
):
    return SupplierReceiptBaseAllocationCandidate(
        source_id=source_id,
        event_date=event_date,
        quantity=Decimal(
            quantity
        ),
    )


def base_target(
    source_id: int,
    amount: str,
    *,
    event_date=D1,
):
    return SupplierReceiptBaseAllocationTarget(
        source_id=source_id,
        event_date=event_date,
        amount=Decimal(
            amount
        ),
        currency_code="UAH",
    )


def vat(
    source_id: int,
    amount: str,
    *,
    event_date=D1,
):
    return SupplierVatLiabilityComponent(
        source_id=source_id,
        event_date=event_date,
        amount=Decimal(
            amount
        ),
    )


def test_full_receipt_base_allocation_closes_exactly():
    targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100.00"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "1",
                ),
            ),
        )
    )

    assert len(targets) == 1
    assert (
        targets[0].amount
        == Decimal("100.00")
    )


def test_three_partial_allocations_use_cumulative_delta():
    targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("3"),
            receipt_base_amount=Decimal("250.00"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "1",
                ),
                allocation(
                    2,
                    "1",
                ),
                allocation(
                    3,
                    "1",
                ),
            ),
        )
    )

    assert tuple(
        target.amount
        for target in targets
    ) == (
        Decimal("83.33"),
        Decimal("83.34"),
        Decimal("83.33"),
    )

    assert sum(
        (
            target.amount
            for target in targets
        ),
        Decimal("0.00"),
    ) == Decimal("250.00")


def test_partial_allocation_is_proportionally_capped():
    targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("3"),
            receipt_base_amount=Decimal("250.00"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "1",
                ),
                allocation(
                    2,
                    "1",
                ),
            ),
        )
    )

    assert tuple(
        target.amount
        for target in targets
    ) == (
        Decimal("83.33"),
        Decimal("83.34"),
    )

    assert sum(
        (
            target.amount
            for target in targets
        ),
        Decimal("0.00"),
    ) == Decimal("166.67")


def test_unsorted_allocations_are_deterministic():
    targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("3"),
            receipt_base_amount=Decimal("250.00"),
            currency_code="uah",
            candidates=(
                allocation(
                    3,
                    "1",
                ),
                allocation(
                    1,
                    "1",
                ),
                allocation(
                    2,
                    "1",
                ),
            ),
        )
    )

    assert tuple(
        target.source_id
        for target in targets
    ) == (
        1,
        2,
        3,
    )

    assert tuple(
        target.amount
        for target in targets
    ) == (
        Decimal("83.33"),
        Decimal("83.34"),
        Decimal("83.33"),
    )


def test_allocation_event_date_is_preserved():
    targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100.00"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "1",
                    event_date=D2,
                ),
            ),
        )
    )

    assert (
        targets[0].event_date
        == D2
    )


def test_allocation_over_receipt_quantity_is_rejected():
    with pytest.raises(
        SupplierEconomicLiabilityAmountError,
        match="exceeds",
    ):
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "0.60",
                ),
                allocation(
                    2,
                    "0.50",
                ),
            ),
        )


def test_duplicate_allocation_source_is_rejected():
    with pytest.raises(
        SupplierEconomicLiabilitySourceError,
        match="unique",
    ):
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "0.50",
                ),
                allocation(
                    1,
                    "0.50",
                ),
            ),
        )


@pytest.mark.parametrize(
    "quantity",
    (
        "0",
        "-1",
    ),
)
def test_nonpositive_allocation_quantity_is_rejected(
    quantity,
):
    with pytest.raises(
        SupplierEconomicLiabilityAmountError
    ):
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    quantity,
                ),
            ),
        )


def test_non_vat_liability_equals_base():
    candidates = (
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(),
            currency_code="UAH",
        )
    )

    assert len(candidates) == 1

    assert (
        candidates[0].amount
        == Decimal("100.00")
    )


def test_vat_liability_equals_base_plus_bridge():
    candidates = (
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(
                vat(
                    1,
                    "20.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert (
        candidates[0].amount
        == Decimal("120.00")
    )


def test_multiple_vat_components_are_summed():
    candidates = (
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(
                vat(
                    1,
                    "12.00",
                ),
                vat(
                    1,
                    "8.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert (
        candidates[0].amount
        == Decimal("120.00")
    )


def test_three_inclusive_allocations_close_to_gross_300():
    base_targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("3"),
            receipt_base_amount=Decimal("250.00"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "1",
                ),
                allocation(
                    2,
                    "1",
                ),
                allocation(
                    3,
                    "1",
                ),
            ),
        )
    )

    candidates = (
        build_supplier_economic_liability_candidates(
            base_targets=base_targets,
            vat_components=(
                vat(
                    1,
                    "16.67",
                ),
                vat(
                    2,
                    "16.66",
                ),
                vat(
                    3,
                    "16.67",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert tuple(
        candidate.amount
        for candidate in candidates
    ) == (
        Decimal("100.00"),
        Decimal("100.00"),
        Decimal("100.00"),
    )

    assert sum(
        (
            candidate.amount
            for candidate in candidates
        ),
        Decimal("0.00"),
    ) == Decimal("300.00")


def test_vat_component_without_base_is_rejected():
    with pytest.raises(
        SupplierEconomicLiabilitySourceError,
        match="no matching",
    ):
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(
                vat(
                    2,
                    "20.00",
                ),
            ),
            currency_code="UAH",
        )


def test_vat_date_must_match_receipt_date():
    with pytest.raises(
        SupplierEconomicLiabilitySourceError,
        match="event_date",
    ):
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                    event_date=D1,
                ),
            ),
            vat_components=(
                vat(
                    1,
                    "20.00",
                    event_date=D2,
                ),
            ),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    "amount",
    (
        "0",
        "-1",
    ),
)
def test_nonpositive_vat_component_is_rejected(
    amount,
):
    with pytest.raises(
        SupplierEconomicLiabilityAmountError
    ):
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(
                vat(
                    1,
                    amount,
                ),
            ),
            currency_code="UAH",
        )


def test_duplicate_base_source_is_rejected():
    with pytest.raises(
        SupplierEconomicLiabilitySourceError,
        match="unique",
    ):
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "50.00",
                ),
                base_target(
                    1,
                    "50.00",
                ),
            ),
            vat_components=(),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    "currency",
    (
        "",
        "UA",
        "UAHH",
        "1AH",
    ),
)
def test_invalid_currency_is_rejected(
    currency,
):
    with pytest.raises(
        SupplierEconomicLiabilityCurrencyError
    ):
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100"),
            currency_code=currency,
            candidates=(
                allocation(
                    1,
                    "1",
                ),
            ),
        )

import app.services.supplier_economic_liability_calculation_service as pvc_liability_service


PVC_D1 = date(
    2026,
    9,
    1,
)

PVC_D2 = date(
    2026,
    9,
    2,
)

PVC_D3 = date(
    2026,
    9,
    3,
)


def pvc_base(
    source_id: int,
    amount: str,
    *,
    event_date=PVC_D1,
):
    return (
        pvc_liability_service
        .SupplierReceiptBaseAllocationTarget(
            source_id=source_id,
            event_date=event_date,
            amount=Decimal(
                amount
            ),
            currency_code="UAH",
        )
    )


def pvc_vat(
    source_id: int,
    amount: str,
    *,
    event_date=PVC_D1,
):
    return (
        pvc_liability_service
        .SupplierVatLiabilityComponent(
            source_id=source_id,
            event_date=event_date,
            amount=Decimal(
                amount
            ),
        )
    )


def pvc_adjustment(
    source_id: int,
    amount: str,
    *,
    recognition_date=PVC_D2,
    currency_code="UAH",
):
    return (
        pvc_liability_service
        .SupplierEconomicLiabilityBaseAdjustment(
            source_id=source_id,
            recognition_date=(
                recognition_date
            ),
            base_delta=Decimal(
                amount
            ),
            currency_code=(
                currency_code
            ),
        )
    )


def test_value_correction_invoice_netting_no_adjustment_preserves_truth():
    result = (
        pvc_liability_service
        .build_supplier_economic_liability_candidates_with_base_adjustments(
            base_targets=(
                pvc_base(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(),
            base_adjustments=(),
            currency_code="UAH",
        )
    )

    assert result == (
        pvc_liability_service
        .SupplierEconomicLiabilityCandidate(
            source_id=1,
            event_date=PVC_D1,
            amount=Decimal(
                "100.00"
            ),
        ),
    )


def test_value_correction_invoice_netting_price_decrease():
    result = (
        pvc_liability_service
        .build_supplier_economic_liability_candidates_with_base_adjustments(
            base_targets=(
                pvc_base(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(),
            base_adjustments=(
                pvc_adjustment(
                    1,
                    "-10.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert len(
        result
    ) == 1

    assert (
        result[0].amount
        == Decimal(
            "90.00"
        )
    )


def test_value_correction_invoice_netting_after_partial_return():
    result = (
        pvc_liability_service
        .build_supplier_economic_liability_candidates_with_base_adjustments(
            base_targets=(
                pvc_base(
                    1,
                    "50.00",
                ),
            ),
            vat_components=(),
            base_adjustments=(
                pvc_adjustment(
                    1,
                    "-10.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert (
        result[0].amount
        == Decimal(
            "40.00"
        )
    )


def test_value_correction_invoice_netting_full_return_then_increase():
    result = (
        pvc_liability_service
        .build_supplier_economic_liability_candidates_with_base_adjustments(
            base_targets=(
                pvc_base(
                    1,
                    "0.00",
                ),
            ),
            vat_components=(),
            base_adjustments=(
                pvc_adjustment(
                    1,
                    "10.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert result == (
        pvc_liability_service
        .SupplierEconomicLiabilityCandidate(
            source_id=1,
            event_date=PVC_D1,
            amount=Decimal(
                "10.00"
            ),
        ),
    )


def test_value_correction_invoice_netting_full_return_then_decrease_is_debit_only():
    result = (
        pvc_liability_service
        .build_supplier_economic_liability_candidates_with_base_adjustments(
            base_targets=(
                pvc_base(
                    1,
                    "0.00",
                ),
            ),
            vat_components=(),
            base_adjustments=(
                pvc_adjustment(
                    1,
                    "-10.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert result == ()


def test_value_correction_invoice_netting_offsets_current_vat_before_clipping():
    result = (
        pvc_liability_service
        .build_supplier_economic_liability_candidates_with_base_adjustments(
            base_targets=(
                pvc_base(
                    1,
                    "0.00",
                ),
            ),
            vat_components=(
                pvc_vat(
                    1,
                    "20.00",
                ),
            ),
            base_adjustments=(
                pvc_adjustment(
                    1,
                    "-10.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert result == (
        pvc_liability_service
        .SupplierEconomicLiabilityCandidate(
            source_id=1,
            event_date=PVC_D1,
            amount=Decimal(
                "10.00"
            ),
        ),
    )


def test_value_correction_invoice_netting_negative_source_reduces_other_source():
    result = (
        pvc_liability_service
        .build_supplier_economic_liability_candidates_with_base_adjustments(
            base_targets=(
                pvc_base(
                    1,
                    "100.00",
                    event_date=PVC_D1,
                ),
                pvc_base(
                    2,
                    "0.00",
                    event_date=PVC_D2,
                ),
            ),
            vat_components=(),
            base_adjustments=(
                pvc_adjustment(
                    2,
                    "-50.00",
                    recognition_date=PVC_D3,
                ),
            ),
            currency_code="UAH",
        )
    )

    assert result == (
        pvc_liability_service
        .SupplierEconomicLiabilityCandidate(
            source_id=1,
            event_date=PVC_D1,
            amount=Decimal(
                "50.00"
            ),
        ),
    )

    assert sum(
        (
            candidate.amount
            for candidate in result
        ),
        Decimal("0"),
    ) == Decimal(
        "50.00"
    )


def test_value_correction_invoice_netting_earlier_negative_uses_later_positive():
    result = (
        pvc_liability_service
        .build_supplier_economic_liability_candidates_with_base_adjustments(
            base_targets=(
                pvc_base(
                    1,
                    "0.00",
                    event_date=PVC_D1,
                ),
                pvc_base(
                    2,
                    "100.00",
                    event_date=PVC_D2,
                ),
            ),
            vat_components=(),
            base_adjustments=(
                pvc_adjustment(
                    1,
                    "-50.00",
                    recognition_date=PVC_D2,
                ),
            ),
            currency_code="UAH",
        )
    )

    assert result == (
        pvc_liability_service
        .SupplierEconomicLiabilityCandidate(
            source_id=2,
            event_date=PVC_D2,
            amount=Decimal(
                "50.00"
            ),
        ),
    )


def test_value_correction_invoice_netting_multiple_adjustments_same_source_sum():
    result = (
        pvc_liability_service
        .build_supplier_economic_liability_candidates_with_base_adjustments(
            base_targets=(
                pvc_base(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(),
            base_adjustments=(
                pvc_adjustment(
                    1,
                    "-30.00",
                ),
                pvc_adjustment(
                    1,
                    "5.00",
                    recognition_date=PVC_D3,
                ),
            ),
            currency_code="UAH",
        )
    )

    assert (
        result[0].amount
        == Decimal(
            "75.00"
        )
    )


def test_value_correction_invoice_netting_zero_aggregate_adjustment_is_stable():
    result = (
        pvc_liability_service
        .build_supplier_economic_liability_candidates_with_base_adjustments(
            base_targets=(
                pvc_base(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(),
            base_adjustments=(
                pvc_adjustment(
                    1,
                    "-10.00",
                ),
                pvc_adjustment(
                    1,
                    "10.00",
                    recognition_date=PVC_D3,
                ),
            ),
            currency_code="UAH",
        )
    )

    assert (
        result[0].amount
        == Decimal(
            "100.00"
        )
    )


def test_value_correction_invoice_netting_unknown_source_fails_closed():
    with pytest.raises(
        pvc_liability_service
        .SupplierEconomicLiabilitySourceError,
        match=(
            "no matching receipt-base source"
        ),
    ):
        (
            pvc_liability_service
            .build_supplier_economic_liability_candidates_with_base_adjustments(
                base_targets=(
                    pvc_base(
                        1,
                        "100.00",
                    ),
                ),
                vat_components=(),
                base_adjustments=(
                    pvc_adjustment(
                        2,
                        "-10.00",
                    ),
                ),
                currency_code="UAH",
            )
        )


def test_value_correction_invoice_netting_rejects_pre_receipt_recognition():
    with pytest.raises(
        pvc_liability_service
        .SupplierEconomicLiabilitySourceError,
        match=(
            "cannot precede receipt economic date"
        ),
    ):
        (
            pvc_liability_service
            .build_supplier_economic_liability_candidates_with_base_adjustments(
                base_targets=(
                    pvc_base(
                        1,
                        "100.00",
                        event_date=PVC_D2,
                    ),
                ),
                vat_components=(),
                base_adjustments=(
                    pvc_adjustment(
                        1,
                        "-10.00",
                        recognition_date=PVC_D1,
                    ),
                ),
                currency_code="UAH",
            )
        )


def test_value_correction_invoice_netting_currency_mismatch_fails_closed():
    with pytest.raises(
        pvc_liability_service
        .SupplierEconomicLiabilityCurrencyError,
        match=(
            "adjustment currency"
        ),
    ):
        (
            pvc_liability_service
            .build_supplier_economic_liability_candidates_with_base_adjustments(
                base_targets=(
                    pvc_base(
                        1,
                        "100.00",
                    ),
                ),
                vat_components=(),
                base_adjustments=(
                    pvc_adjustment(
                        1,
                        "-10.00",
                        currency_code="EUR",
                    ),
                ),
                currency_code="UAH",
            )
        )
