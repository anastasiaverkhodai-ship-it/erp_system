from datetime import date
from decimal import Decimal

import pytest

from app.services.purchase_value_correction_allocation_calculation_service import (
    PurchaseValueCorrectionAllocationCandidate,
    PurchaseValueCorrectionAllocationCapacityError,
    PurchaseValueCorrectionAllocationDataIntegrityError,
    build_purchase_value_correction_allocation_targets,
)


D1 = date(
    2026,
    9,
    1,
)

D2 = date(
    2026,
    9,
    2,
)

D5 = date(
    2026,
    9,
    5,
)


def candidate(
    source_id: int,
    event_date: date,
    quantity: str,
):
    return (
        PurchaseValueCorrectionAllocationCandidate(
            invoice_fulfillment_allocation_id=(
                source_id
            ),
            receipt_event_date=event_date,
            quantity=Decimal(
                quantity
            ),
        )
    )


def build(
    *,
    correction_date=D2,
    invoice_quantity="2",
    original_gross="0.03",
    original_tax="0.00",
    corrected_gross="0.02",
    corrected_tax="0.00",
    candidates,
):
    return (
        build_purchase_value_correction_allocation_targets(
            trade_value_correction_event_id=11,
            correction_date=correction_date,
            invoice_line_quantity=Decimal(
                invoice_quantity
            ),
            original_gross_amount=Decimal(
                original_gross
            ),
            original_tax_amount=Decimal(
                original_tax
            ),
            corrected_gross_amount=Decimal(
                corrected_gross
            ),
            corrected_tax_amount=Decimal(
                corrected_tax
            ),
            currency_code="UAH",
            candidates=candidates,
        )
    )


def test_full_single_source_decrease():
    targets = build(
        invoice_quantity="1",
        original_gross="120.00",
        original_tax="20.00",
        corrected_gross="96.00",
        corrected_tax="16.00",
        candidates=(
            candidate(
                7,
                D1,
                "1",
            ),
        ),
    )

    assert len(
        targets
    ) == 1

    target = targets[0]

    assert (
        target.trade_value_correction_event_id
        == 11
    )

    assert (
        target.invoice_fulfillment_allocation_id
        == 7
    )

    assert (
        target.original_allocated_base_amount
        == Decimal("100.00")
    )

    assert (
        target.corrected_allocated_base_amount
        == Decimal("80.00")
    )

    assert (
        target.allocated_base_delta
        == Decimal("-20.00")
    )

    assert target.is_noop is False


def test_full_single_source_increase():
    targets = build(
        invoice_quantity="1",
        original_gross="96.00",
        original_tax="16.00",
        corrected_gross="120.00",
        corrected_tax="20.00",
        candidates=(
            candidate(
                7,
                D1,
                "1",
            ),
        ),
    )

    target = targets[0]

    assert (
        target.original_allocated_base_amount
        == Decimal("80.00")
    )

    assert (
        target.corrected_allocated_base_amount
        == Decimal("100.00")
    )

    assert (
        target.allocated_base_delta
        == Decimal("20.00")
    )


def test_correction_after_receipt_uses_correction_date():
    targets = build(
        correction_date=D5,
        candidates=(
            candidate(
                1,
                D1,
                "1",
            ),
        ),
    )

    assert (
        targets[0].recognition_date
        == D5
    )


def test_correction_before_future_receipt_uses_receipt_date():
    targets = build(
        correction_date=D1,
        candidates=(
            candidate(
                1,
                D5,
                "1",
            ),
        ),
    )

    assert (
        targets[0].recognition_date
        == D5
    )


def test_order_is_receipt_date_then_source_id():
    targets = build(
        correction_date=D5,
        candidates=(
            candidate(
                30,
                D2,
                "0.5",
            ),
            candidate(
                20,
                D1,
                "0.5",
            ),
            candidate(
                10,
                D1,
                "0.5",
            ),
        ),
    )

    assert tuple(
        target.invoice_fulfillment_allocation_id
        for target in targets
    ) == (
        10,
        20,
        30,
    )


def test_cumulative_rounding_exact_full_total():
    targets = build(
        candidates=(
            candidate(
                1,
                D1,
                "1",
            ),
            candidate(
                2,
                D2,
                "1",
            ),
        ),
    )

    assert tuple(
        target.original_allocated_base_amount
        for target in targets
    ) == (
        Decimal("0.02"),
        Decimal("0.01"),
    )

    assert sum(
        (
            target.original_allocated_base_amount
            for target in targets
        ),
        Decimal("0"),
    ) == Decimal("0.03")

    assert sum(
        (
            target.corrected_allocated_base_amount
            for target in targets
        ),
        Decimal("0"),
    ) == Decimal("0.02")


def test_rounding_can_retain_per_source_noop_target():
    targets = build(
        candidates=(
            candidate(
                1,
                D1,
                "1",
            ),
            candidate(
                2,
                D2,
                "1",
            ),
        ),
    )

    assert (
        targets[1].original_allocated_base_amount
        == Decimal("0.01")
    )

    assert (
        targets[1].corrected_allocated_base_amount
        == Decimal("0.01")
    )

    assert targets[1].is_noop is True


def test_partial_fulfillment_represents_only_partial_base():
    targets = build(
        invoice_quantity="4",
        original_gross="100.00",
        original_tax="0.00",
        corrected_gross="80.00",
        corrected_tax="0.00",
        candidates=(
            candidate(
                1,
                D1,
                "1",
            ),
        ),
    )

    assert (
        targets[0].original_allocated_base_amount
        == Decimal("25.00")
    )

    assert (
        targets[0].corrected_allocated_base_amount
        == Decimal("20.00")
    )


def test_future_later_receipt_does_not_change_earlier_slice():
    before = build(
        candidates=(
            candidate(
                1,
                D1,
                "1",
            ),
        ),
    )

    after = build(
        candidates=(
            candidate(
                1,
                D1,
                "1",
            ),
            candidate(
                2,
                D5,
                "1",
            ),
        ),
    )

    assert (
        before[0].invoice_fulfillment_allocation_id
        == after[0].invoice_fulfillment_allocation_id
    )

    assert (
        before[0].original_allocated_base_amount
        == after[0].original_allocated_base_amount
    )

    assert (
        before[0].corrected_allocated_base_amount
        == after[0].corrected_allocated_base_amount
    )


def test_duplicate_source_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionAllocationDataIntegrityError,
    ):
        build(
            candidates=(
                candidate(
                    1,
                    D1,
                    "0.5",
                ),
                candidate(
                    1,
                    D2,
                    "0.5",
                ),
            ),
        )


def test_nonpositive_candidate_quantity_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionAllocationCapacityError,
    ):
        build(
            candidates=(
                candidate(
                    1,
                    D1,
                    "0",
                ),
            ),
        )


def test_allocation_over_invoice_quantity_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionAllocationCapacityError,
    ):
        build(
            invoice_quantity="1",
            candidates=(
                candidate(
                    1,
                    D1,
                    "1",
                ),
                candidate(
                    2,
                    D2,
                    "1",
                ),
            ),
        )


def test_empty_candidates_is_empty_target_set():
    targets = build(
        candidates=(),
    )

    assert targets == ()


def test_invalid_correction_event_id_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionAllocationDataIntegrityError,
    ):
        build_purchase_value_correction_allocation_targets(
            trade_value_correction_event_id=0,
            correction_date=D1,
            invoice_line_quantity=Decimal("1"),
            original_gross_amount=Decimal("10.00"),
            original_tax_amount=Decimal("0.00"),
            corrected_gross_amount=Decimal("9.00"),
            corrected_tax_amount=Decimal("0.00"),
            currency_code="UAH",
            candidates=(),
        )


def test_invalid_commercial_tax_state_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionAllocationDataIntegrityError,
    ):
        build_purchase_value_correction_allocation_targets(
            trade_value_correction_event_id=1,
            correction_date=D1,
            invoice_line_quantity=Decimal("1"),
            original_gross_amount=Decimal("10.00"),
            original_tax_amount=Decimal("11.00"),
            corrected_gross_amount=Decimal("9.00"),
            corrected_tax_amount=Decimal("0.00"),
            currency_code="UAH",
            candidates=(),
        )
