from decimal import Decimal

import pytest

from app.services.purchase_value_correction_moving_average_effective_issue_calculation_service import (
    PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError,
    calculate_moving_average_effective_issue,
)
from app.services.purchase_value_correction_moving_average_peer_effective_projection_service import (
    MovingAveragePeerOnHandOverlay,
    calculate_peer_aware_effective_moving_average_projection,
)


def _projection():
    return (
        calculate_peer_aware_effective_moving_average_projection(
            quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_overlays=(
                MovingAveragePeerOnHandOverlay(
                    allocation_event_id=100,
                    quantity=Decimal("60"),
                    original_valuation_amount=Decimal("600"),
                    corrected_valuation_amount=Decimal("570"),
                ),
                MovingAveragePeerOnHandOverlay(
                    allocation_event_id=101,
                    quantity=Decimal("60"),
                    original_valuation_amount=Decimal("570"),
                    corrected_valuation_amount=Decimal("540"),
                ),
            ),
        )
    )


def test_partial_issue_uses_effective_average():
    result = calculate_moving_average_effective_issue(
        projection=_projection(),
        issue_quantity=Decimal("20"),
    )

    assert (
        result.base_issue_value
        == Decimal("200.00000000")
    )

    assert (
        result.effective_issue_value
        == Decimal("180.00000000")
    )

    assert (
        result.pvc_issue_valuation_delta
        == Decimal("-20.00000000")
    )


def test_partial_issue_moves_pvc_from_on_hand_to_issued():
    result = calculate_moving_average_effective_issue(
        projection=_projection(),
        issue_quantity=Decimal("20"),
    )

    assert (
        result.pvc_on_hand_valuation_delta_before
        == Decimal("-60.00000000")
    )

    assert (
        result.pvc_on_hand_valuation_delta_after
        == Decimal("-40.00000000")
    )


def test_partial_issue_preserves_effective_value():
    result = calculate_moving_average_effective_issue(
        projection=_projection(),
        issue_quantity=Decimal("20"),
    )

    assert (
        result.base_inventory_value_after
        == Decimal("400.00000000")
    )

    assert (
        result.effective_inventory_value_after
        == Decimal("360.00000000")
    )

    assert (
        result.effective_average_unit_cost_after
        == Decimal("9.00000000")
    )


def test_full_depletion_consumes_exact_values():
    result = calculate_moving_average_effective_issue(
        projection=_projection(),
        issue_quantity=Decimal("60"),
    )

    assert (
        result.base_issue_value
        == Decimal("600.00000000")
    )

    assert (
        result.effective_issue_value
        == Decimal("540.00000000")
    )

    assert (
        result.pvc_issue_valuation_delta
        == Decimal("-60.00000000")
    )

    assert result.quantity_after == Decimal("0")

    assert (
        result.base_inventory_value_after
        == Decimal("0E-8")
    )

    assert (
        result.effective_inventory_value_after
        == Decimal("0E-8")
    )

    assert (
        result.pvc_on_hand_valuation_delta_after
        == Decimal("0E-8")
    )


def test_no_pvc_overlay_means_zero_issue_delta():
    projection = (
        calculate_peer_aware_effective_moving_average_projection(
            quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_overlays=(),
        )
    )

    result = calculate_moving_average_effective_issue(
        projection=projection,
        issue_quantity=Decimal("20"),
    )

    assert (
        result.base_issue_value
        == result.effective_issue_value
    )

    assert (
        result.pvc_issue_valuation_delta
        == Decimal("0E-8")
    )


def test_issue_above_quantity_fails():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError
    ):
        calculate_moving_average_effective_issue(
            projection=_projection(),
            issue_quantity=Decimal("61"),
        )


def test_zero_issue_fails():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError
    ):
        calculate_moving_average_effective_issue(
            projection=_projection(),
            issue_quantity=Decimal("0"),
        )


def test_rounding_sensitive_issue_conserves():
    projection = (
        calculate_peer_aware_effective_moving_average_projection(
            quantity=Decimal("3"),
            base_inventory_value=Decimal("10"),
            base_average_unit_cost=Decimal("3.33333333"),
            active_on_hand_overlays=(
                MovingAveragePeerOnHandOverlay(
                    allocation_event_id=100,
                    quantity=Decimal("3"),
                    original_valuation_amount=Decimal("10"),
                    corrected_valuation_amount=Decimal("11"),
                ),
            ),
        )
    )

    result = calculate_moving_average_effective_issue(
        projection=projection,
        issue_quantity=Decimal("1"),
    )

    assert (
        result.effective_issue_value
        == Decimal("3.66666667")
    )

    assert (
        result.effective_inventory_value_after
        == Decimal("7.33333333")
    )

    assert (
        result.pvc_issue_valuation_delta
        == Decimal("0.33333334")
    )
