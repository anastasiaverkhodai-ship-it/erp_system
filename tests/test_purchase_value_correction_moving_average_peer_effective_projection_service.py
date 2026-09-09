from decimal import Decimal

import pytest

from app.services.purchase_value_correction_moving_average_peer_effective_projection_service import (
    MovingAveragePeerOnHandOverlay,
    PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError,
    PurchaseValueCorrectionMovingAveragePeerProjectionStaleError,
    calculate_peer_aware_effective_moving_average_projection,
)


def _overlay(
    allocation_id,
    original,
    corrected,
    *,
    quantity="60",
):
    return MovingAveragePeerOnHandOverlay(
        allocation_event_id=allocation_id,
        quantity=Decimal(quantity),
        original_valuation_amount=Decimal(original),
        corrected_valuation_amount=Decimal(corrected),
    )


def test_no_overlay_returns_base():
    result = (
        calculate_peer_aware_effective_moving_average_projection(
            quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_overlays=(),
        )
    )

    assert (
        result.effective_inventory_value
        == Decimal("600.00000000")
    )

    assert (
        result.effective_average_unit_cost
        == Decimal("10.00000000")
    )

    assert not result.has_pvc_overlay


def test_two_marginal_peer_overlays_are_additive():
    result = (
        calculate_peer_aware_effective_moving_average_projection(
            quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_overlays=(
                _overlay(
                    100,
                    "600",
                    "570",
                ),
                _overlay(
                    101,
                    "570",
                    "540",
                ),
            ),
        )
    )

    assert (
        result.pvc_on_hand_valuation_delta
        == Decimal("-60.00000000")
    )

    assert (
        result.effective_inventory_value
        == Decimal("540.00000000")
    )

    assert (
        result.effective_average_unit_cost
        == Decimal("9.00000000")
    )

    assert (
        result.active_allocation_event_ids
        == (100, 101)
    )


def test_mixed_peer_deltas_are_additive():
    result = (
        calculate_peer_aware_effective_moving_average_projection(
            quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_overlays=(
                _overlay(
                    100,
                    "600",
                    "540",
                ),
                _overlay(
                    101,
                    "540",
                    "555",
                ),
            ),
        )
    )

    assert (
        result.pvc_on_hand_valuation_delta
        == Decimal("-45.00000000")
    )

    assert (
        result.effective_inventory_value
        == Decimal("555.00000000")
    )


def test_stale_peer_quantity_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAveragePeerProjectionStaleError
    ):
        calculate_peer_aware_effective_moving_average_projection(
            quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_overlays=(
                _overlay(
                    100,
                    "600",
                    "540",
                    quantity="70",
                ),
            ),
        )


def test_duplicate_active_overlay_for_allocation_fails():
    with pytest.raises(
        PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError
    ):
        calculate_peer_aware_effective_moving_average_projection(
            quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_overlays=(
                _overlay(
                    100,
                    "600",
                    "570",
                ),
                _overlay(
                    100,
                    "570",
                    "540",
                ),
            ),
        )


def test_negative_effective_inventory_value_fails():
    with pytest.raises(
        PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError
    ):
        calculate_peer_aware_effective_moving_average_projection(
            quantity=Decimal("60"),
            base_inventory_value=Decimal("10"),
            base_average_unit_cost=Decimal("0.16666667"),
            active_on_hand_overlays=(
                _overlay(
                    100,
                    "10",
                    "0",
                ),
                _overlay(
                    101,
                    "0",
                    "-1",
                ),
            ),
        )


def test_zero_quantity_without_overlay_is_valid():
    result = (
        calculate_peer_aware_effective_moving_average_projection(
            quantity=Decimal("0"),
            base_inventory_value=Decimal("0"),
            base_average_unit_cost=Decimal("0"),
            active_on_hand_overlays=(),
        )
    )

    assert result.quantity == Decimal("0")
    assert result.effective_inventory_value == Decimal("0")
    assert result.effective_average_unit_cost == Decimal("0")


def test_zero_quantity_with_overlay_is_stale():
    with pytest.raises(
        PurchaseValueCorrectionMovingAveragePeerProjectionStaleError
    ):
        calculate_peer_aware_effective_moving_average_projection(
            quantity=Decimal("0"),
            base_inventory_value=Decimal("0"),
            base_average_unit_cost=Decimal("0"),
            active_on_hand_overlays=(
                _overlay(
                    100,
                    "1",
                    "0",
                    quantity="0",
                ),
            ),
        )
