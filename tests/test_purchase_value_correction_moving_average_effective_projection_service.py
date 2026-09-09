from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.purchase_value_correction_moving_average_effective_projection_service import (
    PurchaseValueCorrectionMovingAveragePeerAggregationRequiredError,
    PurchaseValueCorrectionMovingAverageProjectionIntegrityError,
    PurchaseValueCorrectionMovingAverageProjectionStaleError,
    calculate_effective_moving_average_projection,
)


def _event(
    *,
    event_id=1,
    allocation_id=100,
    company_id=1,
    product_id=10,
    warehouse_id=20,
    quantity="60",
    original="600",
    corrected="540",
):
    return SimpleNamespace(
        id=event_id,
        company_id=company_id,
        purchase_value_correction_allocation_event_id=(
            allocation_id
        ),
        product_id=product_id,
        warehouse_id=warehouse_id,
        effect_kind="on_hand",
        source_moving_average_movement_id=None,
        source_inventory_cost_entry_id=None,
        quantity=Decimal(
            quantity
        ),
        original_valuation_amount=Decimal(
            original
        ),
        corrected_valuation_amount=Decimal(
            corrected
        ),
    )


def test_no_pvc_overlay_returns_base_projection():
    result = (
        calculate_effective_moving_average_projection(
            company_id=1,
            product_id=10,
            warehouse_id=20,
            base_quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_events=(),
        )
    )

    assert result.quantity == Decimal("60")
    assert (
        result.base_inventory_value
        == Decimal("600.00000000")
    )
    assert (
        result.pvc_on_hand_valuation_delta
        == Decimal("0")
    )
    assert (
        result.effective_inventory_value
        == Decimal("600.00000000")
    )
    assert (
        result.effective_average_unit_cost
        == Decimal("10.00000000")
    )
    assert result.has_pvc_overlay is False


def test_value_decrease_changes_effective_value_and_average():
    result = (
        calculate_effective_moving_average_projection(
            company_id=1,
            product_id=10,
            warehouse_id=20,
            base_quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_events=(
                _event(),
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
    assert result.has_pvc_overlay is True
    assert result.active_on_hand_event_ids == (1,)
    assert (
        result.active_allocation_event_ids
        == (100,)
    )


def test_value_increase_changes_effective_average():
    result = (
        calculate_effective_moving_average_projection(
            company_id=1,
            product_id=10,
            warehouse_id=20,
            base_quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_events=(
                _event(
                    original="600",
                    corrected="660",
                ),
            ),
        )
    )

    assert (
        result.pvc_on_hand_valuation_delta
        == Decimal("60.00000000")
    )
    assert (
        result.effective_inventory_value
        == Decimal("660.00000000")
    )
    assert (
        result.effective_average_unit_cost
        == Decimal("11.00000000")
    )


def test_stale_on_hand_quantity_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageProjectionStaleError
    ):
        calculate_effective_moving_average_projection(
            company_id=1,
            product_id=10,
            warehouse_id=20,
            base_quantity=Decimal("50"),
            base_inventory_value=Decimal("500"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_events=(
                _event(
                    quantity="60",
                ),
            ),
        )


def test_multiple_active_allocations_require_peer_aggregation():
    with pytest.raises(
        PurchaseValueCorrectionMovingAveragePeerAggregationRequiredError
    ):
        calculate_effective_moving_average_projection(
            company_id=1,
            product_id=10,
            warehouse_id=20,
            base_quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_events=(
                _event(
                    event_id=1,
                    allocation_id=100,
                    original="600",
                    corrected="570",
                ),
                _event(
                    event_id=2,
                    allocation_id=101,
                    original="600",
                    corrected="570",
                ),
            ),
        )


def test_multiple_active_events_same_allocation_can_combine():
    result = (
        calculate_effective_moving_average_projection(
            company_id=1,
            product_id=10,
            warehouse_id=20,
            base_quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_events=(
                _event(
                    event_id=1,
                    allocation_id=100,
                    original="600",
                    corrected="570",
                ),
                _event(
                    event_id=2,
                    allocation_id=100,
                    original="570",
                    corrected="550",
                ),
            ),
        )
    )

    assert (
        result.pvc_on_hand_valuation_delta
        == Decimal("-50.00000000")
    )
    assert (
        result.effective_inventory_value
        == Decimal("550.00000000")
    )


def test_overlay_cannot_make_effective_value_negative():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageProjectionIntegrityError
    ):
        calculate_effective_moving_average_projection(
            company_id=1,
            product_id=10,
            warehouse_id=20,
            base_quantity=Decimal("60"),
            base_inventory_value=Decimal("20"),
            base_average_unit_cost=Decimal("0.33333333"),
            active_on_hand_events=(
                _event(
                    original="100",
                    corrected="0",
                ),
            ),
        )


def test_zero_quantity_requires_zero_base_state():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageProjectionIntegrityError
    ):
        calculate_effective_moving_average_projection(
            company_id=1,
            product_id=10,
            warehouse_id=20,
            base_quantity=Decimal("0"),
            base_inventory_value=Decimal("1"),
            base_average_unit_cost=Decimal("0"),
            active_on_hand_events=(),
        )


def test_zero_quantity_without_overlay_is_valid():
    result = (
        calculate_effective_moving_average_projection(
            company_id=1,
            product_id=10,
            warehouse_id=20,
            base_quantity=Decimal("0"),
            base_inventory_value=Decimal("0"),
            base_average_unit_cost=Decimal("0"),
            active_on_hand_events=(),
        )
    )

    assert result.quantity == Decimal("0")
    assert (
        result.effective_inventory_value
        == Decimal("0E-8")
    )
    assert (
        result.effective_average_unit_cost
        == Decimal("0E-8")
    )


def test_on_hand_stream_provenance_mismatch_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageProjectionIntegrityError
    ):
        calculate_effective_moving_average_projection(
            company_id=1,
            product_id=10,
            warehouse_id=20,
            base_quantity=Decimal("60"),
            base_inventory_value=Decimal("600"),
            base_average_unit_cost=Decimal("10"),
            active_on_hand_events=(
                _event(
                    product_id=999,
                ),
            ),
        )


def test_effective_average_uses_eight_decimal_rounding():
    result = (
        calculate_effective_moving_average_projection(
            company_id=1,
            product_id=10,
            warehouse_id=20,
            base_quantity=Decimal("3"),
            base_inventory_value=Decimal("10"),
            base_average_unit_cost=Decimal("3.33333333"),
            active_on_hand_events=(
                _event(
                    quantity="3",
                    original="10",
                    corrected="11",
                ),
            ),
        )
    )

    assert (
        result.effective_inventory_value
        == Decimal("11.00000000")
    )
    assert (
        result.effective_average_unit_cost
        == Decimal("3.66666667")
    )
