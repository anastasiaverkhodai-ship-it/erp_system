from datetime import date
from decimal import Decimal

import pytest

from app.services.sales_recognition_calculation_service import (
    DuplicateSalesRecognitionSourceError,
    SalesRecognitionAmountError,
    SalesRecognitionCandidate,
    SalesRecognitionDataIntegrityError,
    SalesRecognitionQuantityError,
    SalesRecognitionTarget,
    build_sales_recognition_targets,
    calculate_sales_recognition_slice,
    order_sales_recognition_reconciliations,
)


D1 = date(2026, 9, 1)
D2 = date(2026, 9, 2)


def test_full_invoice_line_recognition():
    result = calculate_sales_recognition_slice(
        invoice_line_quantity=Decimal("10"),
        allocated_quantity_before=Decimal("0"),
        allocation_quantity=Decimal("10"),
        invoice_line_gross_amount=Decimal("120.00"),
        invoice_line_tax_amount=Decimal("20.00"),
        currency_code="UAH",
    )

    assert result.quantity == Decimal("10")
    assert result.gross_amount == Decimal("120.00")
    assert result.tax_amount == Decimal("20.00")


def test_partial_invoice_line_recognition():
    result = calculate_sales_recognition_slice(
        invoice_line_quantity=Decimal("10"),
        allocated_quantity_before=Decimal("0"),
        allocation_quantity=Decimal("3"),
        invoice_line_gross_amount=Decimal("120.00"),
        invoice_line_tax_amount=Decimal("20.00"),
        currency_code="UAH",
    )

    assert result.quantity == Decimal("3")
    assert result.gross_amount == Decimal("36.00")
    assert result.tax_amount == Decimal("6.00")


def test_second_partial_slice_uses_cumulative_delta():
    result = calculate_sales_recognition_slice(
        invoice_line_quantity=Decimal("10"),
        allocated_quantity_before=Decimal("3"),
        allocation_quantity=Decimal("2"),
        invoice_line_gross_amount=Decimal("120.00"),
        invoice_line_tax_amount=Decimal("20.00"),
        currency_code="UAH",
    )

    assert result.quantity == Decimal("2")
    assert result.gross_amount == Decimal("24.00")
    assert result.tax_amount == Decimal("4.00")


def test_three_rounded_slices_reconcile_exactly():
    first = calculate_sales_recognition_slice(
        invoice_line_quantity=Decimal("3"),
        allocated_quantity_before=Decimal("0"),
        allocation_quantity=Decimal("1"),
        invoice_line_gross_amount=Decimal("100.00"),
        invoice_line_tax_amount=Decimal("20.00"),
        currency_code="UAH",
    )

    second = calculate_sales_recognition_slice(
        invoice_line_quantity=Decimal("3"),
        allocated_quantity_before=Decimal("1"),
        allocation_quantity=Decimal("1"),
        invoice_line_gross_amount=Decimal("100.00"),
        invoice_line_tax_amount=Decimal("20.00"),
        currency_code="UAH",
    )

    third = calculate_sales_recognition_slice(
        invoice_line_quantity=Decimal("3"),
        allocated_quantity_before=Decimal("2"),
        allocation_quantity=Decimal("1"),
        invoice_line_gross_amount=Decimal("100.00"),
        invoice_line_tax_amount=Decimal("20.00"),
        currency_code="UAH",
    )

    assert (
        first.gross_amount,
        second.gross_amount,
        third.gross_amount,
    ) == (
        Decimal("33.33"),
        Decimal("33.34"),
        Decimal("33.33"),
    )

    assert (
        first.tax_amount,
        second.tax_amount,
        third.tax_amount,
    ) == (
        Decimal("6.67"),
        Decimal("6.66"),
        Decimal("6.67"),
    )

    assert (
        first.gross_amount
        + second.gross_amount
        + third.gross_amount
        == Decimal("100.00")
    )

    assert (
        first.tax_amount
        + second.tax_amount
        + third.tax_amount
        == Decimal("20.00")
    )


def test_non_vat_line_preserves_zero_tax():
    result = calculate_sales_recognition_slice(
        invoice_line_quantity=Decimal("4"),
        allocated_quantity_before=Decimal("1"),
        allocation_quantity=Decimal("2"),
        invoice_line_gross_amount=Decimal("80.00"),
        invoice_line_tax_amount=Decimal("0.00"),
        currency_code="UAH",
    )

    assert result.gross_amount == Decimal("40.00")
    assert result.tax_amount == Decimal("0.00")


def test_over_allocation_is_rejected():
    with pytest.raises(
        SalesRecognitionQuantityError,
        match="exceeds invoice line quantity",
    ):
        calculate_sales_recognition_slice(
            invoice_line_quantity=Decimal("10"),
            allocated_quantity_before=Decimal("8"),
            allocation_quantity=Decimal("3"),
            invoice_line_gross_amount=Decimal("120.00"),
            invoice_line_tax_amount=Decimal("20.00"),
            currency_code="UAH",
        )


def test_negative_allocated_before_is_rejected():
    with pytest.raises(
        SalesRecognitionQuantityError,
        match="cannot be negative",
    ):
        calculate_sales_recognition_slice(
            invoice_line_quantity=Decimal("10"),
            allocated_quantity_before=Decimal("-1"),
            allocation_quantity=Decimal("1"),
            invoice_line_gross_amount=Decimal("120.00"),
            invoice_line_tax_amount=Decimal("20.00"),
            currency_code="UAH",
        )


def test_tax_cannot_exceed_gross():
    with pytest.raises(
        SalesRecognitionAmountError,
        match="cannot exceed gross amount",
    ):
        calculate_sales_recognition_slice(
            invoice_line_quantity=Decimal("1"),
            allocated_quantity_before=Decimal("0"),
            allocation_quantity=Decimal("1"),
            invoice_line_gross_amount=Decimal("20.00"),
            invoice_line_tax_amount=Decimal("21.00"),
            currency_code="UAH",
        )


def test_targets_use_economic_date_then_source_id_order():
    targets = build_sales_recognition_targets(
        invoice_line_quantity=Decimal("3"),
        invoice_line_gross_amount=Decimal("100.00"),
        invoice_line_tax_amount=Decimal("20.00"),
        currency_code="UAH",
        candidates=(
            SalesRecognitionCandidate(
                source_id=30,
                event_date=D2,
                quantity=Decimal("1"),
            ),
            SalesRecognitionCandidate(
                source_id=20,
                event_date=D1,
                quantity=Decimal("1"),
            ),
            SalesRecognitionCandidate(
                source_id=10,
                event_date=D1,
                quantity=Decimal("1"),
            ),
        ),
    )

    assert [
        target.source_id
        for target in targets
    ] == [
        10,
        20,
        30,
    ]

    assert [
        target.gross_amount
        for target in targets
    ] == [
        Decimal("33.33"),
        Decimal("33.34"),
        Decimal("33.33"),
    ]

    assert [
        target.tax_amount
        for target in targets
    ] == [
        Decimal("6.67"),
        Decimal("6.66"),
        Decimal("6.67"),
    ]


def test_duplicate_candidate_source_is_rejected():
    with pytest.raises(
        DuplicateSalesRecognitionSourceError,
        match="Duplicate Sales recognition source",
    ):
        build_sales_recognition_targets(
            invoice_line_quantity=Decimal("2"),
            invoice_line_gross_amount=Decimal("100.00"),
            invoice_line_tax_amount=Decimal("20.00"),
            currency_code="UAH",
            candidates=(
                SalesRecognitionCandidate(
                    source_id=1,
                    event_date=D1,
                    quantity=Decimal("1"),
                ),
                SalesRecognitionCandidate(
                    source_id=1,
                    event_date=D2,
                    quantity=Decimal("1"),
                ),
            ),
        )


def test_candidate_total_cannot_exceed_invoice_quantity():
    with pytest.raises(
        SalesRecognitionQuantityError,
        match="exceeds invoice line quantity",
    ):
        build_sales_recognition_targets(
            invoice_line_quantity=Decimal("2"),
            invoice_line_gross_amount=Decimal("100.00"),
            invoice_line_tax_amount=Decimal("20.00"),
            currency_code="UAH",
            candidates=(
                SalesRecognitionCandidate(
                    source_id=1,
                    event_date=D1,
                    quantity=Decimal("1.5"),
                ),
                SalesRecognitionCandidate(
                    source_id=2,
                    event_date=D2,
                    quantity=Decimal("1"),
                ),
            ),
        )


def test_reversal_reassigns_rounding_to_remaining_source():
    current = build_sales_recognition_targets(
        invoice_line_quantity=Decimal("3"),
        invoice_line_gross_amount=Decimal("100.00"),
        invoice_line_tax_amount=Decimal("20.00"),
        currency_code="UAH",
        candidates=(
            SalesRecognitionCandidate(
                source_id=1,
                event_date=D1,
                quantity=Decimal("1"),
            ),
            SalesRecognitionCandidate(
                source_id=2,
                event_date=D1,
                quantity=Decimal("1"),
            ),
            SalesRecognitionCandidate(
                source_id=3,
                event_date=D1,
                quantity=Decimal("1"),
            ),
        ),
    )

    desired = build_sales_recognition_targets(
        invoice_line_quantity=Decimal("3"),
        invoice_line_gross_amount=Decimal("100.00"),
        invoice_line_tax_amount=Decimal("20.00"),
        currency_code="UAH",
        candidates=(
            SalesRecognitionCandidate(
                source_id=1,
                event_date=D1,
                quantity=Decimal("1"),
            ),
            SalesRecognitionCandidate(
                source_id=3,
                event_date=D1,
                quantity=Decimal("1"),
            ),
        ),
    )

    assert [
        (
            target.source_id,
            target.gross_amount,
            target.tax_amount,
        )
        for target in current
    ] == [
        (
            1,
            Decimal("33.33"),
            Decimal("6.67"),
        ),
        (
            2,
            Decimal("33.34"),
            Decimal("6.66"),
        ),
        (
            3,
            Decimal("33.33"),
            Decimal("6.67"),
        ),
    ]

    assert [
        (
            target.source_id,
            target.gross_amount,
            target.tax_amount,
        )
        for target in desired
    ] == [
        (
            1,
            Decimal("33.33"),
            Decimal("6.67"),
        ),
        (
            3,
            Decimal("33.34"),
            Decimal("6.66"),
        ),
    ]

    assert sum(
        (
            target.gross_amount
            for target in desired
        ),
        Decimal("0"),
    ) == Decimal("66.67")


def test_reconciliation_orders_removed_source_before_growth():
    current = (
        SalesRecognitionTarget(
            source_id=1,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("33.33"),
            tax_amount=Decimal("6.67"),
        ),
        SalesRecognitionTarget(
            source_id=2,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("33.34"),
            tax_amount=Decimal("6.66"),
        ),
        SalesRecognitionTarget(
            source_id=3,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("33.33"),
            tax_amount=Decimal("6.67"),
        ),
    )

    desired = (
        current[0],
        SalesRecognitionTarget(
            source_id=3,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("33.34"),
            tax_amount=Decimal("6.66"),
        ),
    )

    adjustments = (
        order_sales_recognition_reconciliations(
            current_targets=current,
            desired_targets=desired,
        )
    )

    assert len(adjustments) == 2

    assert adjustments[0].source_id == 2
    assert adjustments[0].is_zero

    assert adjustments[1].source_id == 3
    assert adjustments[1].gross_amount == Decimal(
        "33.34"
    )
    assert adjustments[1].tax_amount == Decimal(
        "6.66"
    )


def test_reconciliation_orders_amount_decrease_before_increase():
    current = (
        SalesRecognitionTarget(
            source_id=1,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("33.34"),
            tax_amount=Decimal("6.67"),
        ),
        SalesRecognitionTarget(
            source_id=2,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("33.33"),
            tax_amount=Decimal("6.66"),
        ),
    )

    desired = (
        SalesRecognitionTarget(
            source_id=1,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("33.33"),
            tax_amount=Decimal("6.66"),
        ),
        SalesRecognitionTarget(
            source_id=2,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("33.34"),
            tax_amount=Decimal("6.67"),
        ),
    )

    adjustments = (
        order_sales_recognition_reconciliations(
            current_targets=current,
            desired_targets=desired,
        )
    )

    assert [
        target.source_id
        for target in adjustments
    ] == [
        1,
        2,
    ]


def test_reconciliation_noop_returns_empty_tuple():
    current = (
        SalesRecognitionTarget(
            source_id=1,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("50.00"),
            tax_amount=Decimal("10.00"),
        ),
    )

    assert (
        order_sales_recognition_reconciliations(
            current_targets=current,
            desired_targets=current,
        )
        == ()
    )


def test_same_source_event_date_cannot_change():
    current = (
        SalesRecognitionTarget(
            source_id=1,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("50.00"),
            tax_amount=Decimal("10.00"),
        ),
    )

    desired = (
        SalesRecognitionTarget(
            source_id=1,
            event_date=D2,
            quantity=Decimal("1"),
            gross_amount=Decimal("50.00"),
            tax_amount=Decimal("10.00"),
        ),
    )

    with pytest.raises(
        SalesRecognitionDataIntegrityError,
        match="event_date changed unexpectedly",
    ):
        order_sales_recognition_reconciliations(
            current_targets=current,
            desired_targets=desired,
        )


def test_same_source_quantity_cannot_change():
    current = (
        SalesRecognitionTarget(
            source_id=1,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("50.00"),
            tax_amount=Decimal("10.00"),
        ),
    )

    desired = (
        SalesRecognitionTarget(
            source_id=1,
            event_date=D1,
            quantity=Decimal("2"),
            gross_amount=Decimal("50.00"),
            tax_amount=Decimal("10.00"),
        ),
    )

    with pytest.raises(
        SalesRecognitionDataIntegrityError,
        match="quantity changed unexpectedly",
    ):
        order_sales_recognition_reconciliations(
            current_targets=current,
            desired_targets=desired,
        )
