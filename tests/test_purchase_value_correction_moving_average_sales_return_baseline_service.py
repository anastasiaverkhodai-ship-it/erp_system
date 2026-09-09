from decimal import Decimal

from app.services.purchase_value_correction_moving_average_sales_return_baseline_service import (
    PurchaseValueCorrectionMovingAverageSalesReturnAllocationBaseline,
    PurchaseValueCorrectionMovingAverageSalesReturnBaselineResult,
)
from app.services.purchase_value_correction_moving_average_sales_return_target_service import (
    MovingAverageSalesReturnBaselineIssuedImpact,
    MovingAverageSalesReturnBaselineOnHandImpact,
)


def test_baseline_result_preserves_exact_issue_provenance():
    issued = (
        MovingAverageSalesReturnBaselineIssuedImpact(
            allocation_event_id=11,
            source_moving_average_movement_id=21,
            source_inventory_cost_entry_id=31,
            source_issue_quantity=Decimal("40"),
            original_valuation_amount=Decimal("40"),
            corrected_valuation_amount=Decimal("36"),
            currency_code="UAH",
        )
    )

    on_hand = (
        MovingAverageSalesReturnBaselineOnHandImpact(
            allocation_event_id=11,
            quantity=Decimal("60"),
            original_valuation_amount=Decimal("60"),
            corrected_valuation_amount=Decimal("54"),
            currency_code="UAH",
        )
    )

    result = (
        PurchaseValueCorrectionMovingAverageSalesReturnBaselineResult(
            allocations=(
                PurchaseValueCorrectionMovingAverageSalesReturnAllocationBaseline(
                    allocation_event_id=11,
                    issued=issued,
                    on_hand=on_hand,
                ),
            ),
        )
    )

    assert (
        result.allocations[0]
        .issued
        .source_inventory_cost_entry_id
        == 31
    )

    assert (
        result.allocations[0]
        .issued
        .source_moving_average_movement_id
        == 21
    )


def test_baseline_can_have_no_on_hand_impact():
    issued = (
        MovingAverageSalesReturnBaselineIssuedImpact(
            allocation_event_id=11,
            source_moving_average_movement_id=21,
            source_inventory_cost_entry_id=31,
            source_issue_quantity=Decimal("40"),
            original_valuation_amount=Decimal("40"),
            corrected_valuation_amount=Decimal("36"),
            currency_code="UAH",
        )
    )

    item = (
        PurchaseValueCorrectionMovingAverageSalesReturnAllocationBaseline(
            allocation_event_id=11,
            issued=issued,
            on_hand=None,
        )
    )

    assert item.on_hand is None


def test_empty_baseline_is_valid_noop():
    result = (
        PurchaseValueCorrectionMovingAverageSalesReturnBaselineResult(
            allocations=(),
        )
    )

    assert result.allocations == ()
