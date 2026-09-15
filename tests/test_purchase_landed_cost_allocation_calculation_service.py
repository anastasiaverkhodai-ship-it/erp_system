from decimal import Decimal

import pytest

from app.services.purchase_landed_cost_allocation_calculation_service import (
    PurchaseLandedCostAllocationCalculationError,
    PurchaseLandedCostAllocationInput,
    calculate_purchase_landed_cost_allocations,
)


def test_allocates_by_receipt_quantity() -> None:
    result = calculate_purchase_landed_cost_allocations(
        total_amount=Decimal("100.00"),
        lines=(
            PurchaseLandedCostAllocationInput(
                fulfillment_line_id=11,
                quantity=Decimal("1"),
            ),
            PurchaseLandedCostAllocationInput(
                fulfillment_line_id=12,
                quantity=Decimal("3"),
            ),
        ),
    )

    assert [x.allocated_amount for x in result] == [
        Decimal("25.00"),
        Decimal("75.00"),
    ]


def test_rounding_remainder_goes_to_final_line() -> None:
    result = calculate_purchase_landed_cost_allocations(
        total_amount=Decimal("10.00"),
        lines=(
            PurchaseLandedCostAllocationInput(1, Decimal("1")),
            PurchaseLandedCostAllocationInput(2, Decimal("1")),
            PurchaseLandedCostAllocationInput(3, Decimal("1")),
        ),
    )

    assert [x.allocated_amount for x in result] == [
        Decimal("3.33"),
        Decimal("3.33"),
        Decimal("3.34"),
    ]
    assert sum(x.allocated_amount for x in result) == Decimal("10.00")


@pytest.mark.parametrize(
    "amount",
    [Decimal("0"), Decimal("-1")],
)
def test_rejects_non_positive_total(amount: Decimal) -> None:
    with pytest.raises(
        PurchaseLandedCostAllocationCalculationError
    ):
        calculate_purchase_landed_cost_allocations(
            total_amount=amount,
            lines=(
                PurchaseLandedCostAllocationInput(
                    1,
                    Decimal("1"),
                ),
            ),
        )


def test_rejects_empty_lines() -> None:
    with pytest.raises(
        PurchaseLandedCostAllocationCalculationError
    ):
        calculate_purchase_landed_cost_allocations(
            total_amount=Decimal("1.00"),
            lines=(),
        )


def test_rejects_duplicate_fulfillment_line() -> None:
    with pytest.raises(
        PurchaseLandedCostAllocationCalculationError
    ):
        calculate_purchase_landed_cost_allocations(
            total_amount=Decimal("1.00"),
            lines=(
                PurchaseLandedCostAllocationInput(
                    1,
                    Decimal("1"),
                ),
                PurchaseLandedCostAllocationInput(
                    1,
                    Decimal("1"),
                ),
            ),
        )


def test_rejects_non_positive_quantity() -> None:
    with pytest.raises(
        PurchaseLandedCostAllocationCalculationError
    ):
        calculate_purchase_landed_cost_allocations(
            total_amount=Decimal("1.00"),
            lines=(
                PurchaseLandedCostAllocationInput(
                    1,
                    Decimal("0"),
                ),
            ),
        )
