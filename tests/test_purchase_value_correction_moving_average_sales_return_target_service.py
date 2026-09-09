from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.services.purchase_value_correction_moving_average_sales_return_target_service import (
    MovingAverageSalesReturnBaselineIssuedImpact,
    MovingAverageSalesReturnBaselineOnHandImpact,
    PurchaseValueCorrectionMovingAverageSalesReturnTargetError,
    build_purchase_value_correction_moving_average_sales_return_targets,
)


@dataclass(
    frozen=True,
    slots=True,
)
class ReturnCandidate:
    return_source_id: int
    return_quantity: Decimal


def issued():
    return (
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


def on_hand():
    return (
        MovingAverageSalesReturnBaselineOnHandImpact(
            allocation_event_id=11,
            quantity=Decimal("60"),
            original_valuation_amount=Decimal("60"),
            corrected_valuation_amount=Decimal("54"),
            currency_code="UAH",
        )
    )


def by_kind(result):
    return {
        target.effect_kind: target
        for target in result.desired_targets
    }


def test_first_partial_return_builds_issued_residual_and_on_hand():
    result = (
        build_purchase_value_correction_moving_average_sales_return_targets(
            baseline_issued=issued(),
            baseline_on_hand=on_hand(),
            active_return_candidates=(
                ReturnCandidate(
                    return_source_id=101,
                    return_quantity=Decimal("10"),
                ),
            ),
        )
    )

    targets = by_kind(result)

    assert (
        targets["issued"].quantity
        == Decimal("30")
    )
    assert (
        targets["issued"].original_valuation_amount
        == Decimal("30.00000000")
    )
    assert (
        targets["issued"].corrected_valuation_amount
        == Decimal("27.00000000")
    )

    assert (
        targets["on_hand"].quantity
        == Decimal("70")
    )
    assert (
        targets["on_hand"].original_valuation_amount
        == Decimal("70.00000000")
    )
    assert (
        targets["on_hand"].corrected_valuation_amount
        == Decimal("63.00000000")
    )

    assert (
        targets["issued"].valuation_delta
        + targets["on_hand"].valuation_delta
        == Decimal("-10.00000000")
    )


def test_second_partial_return_uses_original_baseline_not_current_residual():
    result = (
        build_purchase_value_correction_moving_average_sales_return_targets(
            baseline_issued=issued(),
            baseline_on_hand=on_hand(),
            active_return_candidates=(
                ReturnCandidate(
                    return_source_id=101,
                    return_quantity=Decimal("10"),
                ),
                ReturnCandidate(
                    return_source_id=102,
                    return_quantity=Decimal("5"),
                ),
            ),
        )
    )

    targets = by_kind(result)

    # Correct cumulative state is 40 - (10 + 5) = 25.
    # It must NOT calculate 30 - 15 = 15.
    assert (
        targets["issued"].quantity
        == Decimal("25")
    )

    assert (
        targets["issued"].original_valuation_amount
        == Decimal("25.00000000")
    )
    assert (
        targets["issued"].corrected_valuation_amount
        == Decimal("22.50000000")
    )

    assert (
        targets["on_hand"].quantity
        == Decimal("75")
    )
    assert (
        targets["on_hand"].original_valuation_amount
        == Decimal("75.00000000")
    )
    assert (
        targets["on_hand"].corrected_valuation_amount
        == Decimal("67.50000000")
    )


def test_return_reversal_reduces_active_history_forward_target():
    result = (
        build_purchase_value_correction_moving_average_sales_return_targets(
            baseline_issued=issued(),
            baseline_on_hand=on_hand(),
            active_return_candidates=(
                ReturnCandidate(
                    return_source_id=101,
                    return_quantity=Decimal("10"),
                ),
            ),
        )
    )

    targets = by_kind(result)

    # If a later 5-unit return has been reversed, active history
    # again contains only the original 10-unit return.
    assert (
        targets["issued"].quantity
        == Decimal("30")
    )
    assert (
        targets["on_hand"].quantity
        == Decimal("70")
    )


def test_all_issue_quantity_returned_removes_issued_target():
    result = (
        build_purchase_value_correction_moving_average_sales_return_targets(
            baseline_issued=issued(),
            baseline_on_hand=on_hand(),
            active_return_candidates=(
                ReturnCandidate(
                    return_source_id=101,
                    return_quantity=Decimal("40"),
                ),
            ),
        )
    )

    targets = by_kind(result)

    assert "issued" not in targets

    assert (
        targets["on_hand"].quantity
        == Decimal("100")
    )
    assert (
        targets["on_hand"].original_valuation_amount
        == Decimal("100")
    )
    assert (
        targets["on_hand"].corrected_valuation_amount
        == Decimal("90")
    )


def test_zero_active_returns_restores_pre_return_baseline():
    result = (
        build_purchase_value_correction_moving_average_sales_return_targets(
            baseline_issued=issued(),
            baseline_on_hand=on_hand(),
            active_return_candidates=(),
        )
    )

    targets = by_kind(result)

    assert (
        targets["issued"].quantity
        == Decimal("40")
    )
    assert (
        targets["issued"].original_valuation_amount
        == Decimal("40")
    )
    assert (
        targets["issued"].corrected_valuation_amount
        == Decimal("36")
    )

    assert (
        targets["on_hand"].quantity
        == Decimal("60")
    )
    assert (
        targets["on_hand"].original_valuation_amount
        == Decimal("60")
    )
    assert (
        targets["on_hand"].corrected_valuation_amount
        == Decimal("54")
    )


def test_missing_pre_return_on_hand_is_supported():
    result = (
        build_purchase_value_correction_moving_average_sales_return_targets(
            baseline_issued=issued(),
            baseline_on_hand=None,
            active_return_candidates=(
                ReturnCandidate(
                    return_source_id=101,
                    return_quantity=Decimal("10"),
                ),
            ),
        )
    )

    targets = by_kind(result)

    assert (
        targets["issued"].quantity
        == Decimal("30")
    )
    assert (
        targets["on_hand"].quantity
        == Decimal("10")
    )

    assert (
        targets["on_hand"].original_valuation_amount
        == Decimal("10.00000000")
    )
    assert (
        targets["on_hand"].corrected_valuation_amount
        == Decimal("9.00000000")
    )


def test_price_increase_preserves_total_delta():
    baseline_issued = (
        MovingAverageSalesReturnBaselineIssuedImpact(
            allocation_event_id=11,
            source_moving_average_movement_id=21,
            source_inventory_cost_entry_id=31,
            source_issue_quantity=Decimal("40"),
            original_valuation_amount=Decimal("40"),
            corrected_valuation_amount=Decimal("44"),
            currency_code="UAH",
        )
    )

    baseline_on_hand = (
        MovingAverageSalesReturnBaselineOnHandImpact(
            allocation_event_id=11,
            quantity=Decimal("60"),
            original_valuation_amount=Decimal("60"),
            corrected_valuation_amount=Decimal("66"),
            currency_code="UAH",
        )
    )

    result = (
        build_purchase_value_correction_moving_average_sales_return_targets(
            baseline_issued=baseline_issued,
            baseline_on_hand=baseline_on_hand,
            active_return_candidates=(
                ReturnCandidate(
                    return_source_id=101,
                    return_quantity=Decimal("15"),
                ),
            ),
        )
    )

    assert (
        sum(
            (
                item.valuation_delta
                for item in result.desired_targets
            ),
            Decimal("0"),
        )
        == Decimal("10.00000000")
    )


def test_allocation_mismatch_fails_closed():
    wrong_on_hand = (
        MovingAverageSalesReturnBaselineOnHandImpact(
            allocation_event_id=12,
            quantity=Decimal("60"),
            original_valuation_amount=Decimal("60"),
            corrected_valuation_amount=Decimal("54"),
            currency_code="UAH",
        )
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageSalesReturnTargetError
    ):
        build_purchase_value_correction_moving_average_sales_return_targets(
            baseline_issued=issued(),
            baseline_on_hand=wrong_on_hand,
            active_return_candidates=(),
        )


def test_currency_mismatch_fails_closed():
    wrong_on_hand = (
        MovingAverageSalesReturnBaselineOnHandImpact(
            allocation_event_id=11,
            quantity=Decimal("60"),
            original_valuation_amount=Decimal("60"),
            corrected_valuation_amount=Decimal("54"),
            currency_code="EUR",
        )
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageSalesReturnTargetError
    ):
        build_purchase_value_correction_moving_average_sales_return_targets(
            baseline_issued=issued(),
            baseline_on_hand=wrong_on_hand,
            active_return_candidates=(),
        )
