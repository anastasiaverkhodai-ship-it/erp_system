from datetime import date
from decimal import Decimal

import pytest

from app.models.stock_ledger import StockMovementType
from app.services.purchase_value_correction_moving_average_peer_replay_service import (
    MovingAveragePeerCorrection,
    PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError,
    calculate_purchase_value_correction_moving_average_peer_replay,
)
from app.services.purchase_value_correction_moving_average_replay_calculation_service import (
    MovingAverageReplayMovement,
)


def _peer(
    allocation_id,
    delta,
    *,
    recognition_date=date(2026, 1, 5),
):
    return MovingAveragePeerCorrection(
        allocation_event_id=allocation_id,
        recognition_date=recognition_date,
        allocation_value_delta=Decimal(
            delta
        ),
    )


def _issue(
    *,
    movement_id=2,
    movement_date=date(2026, 1, 3),
    quantity="-1",
    value="-3.33333333",
    balance_quantity_after="2",
    balance_value_after="6.66666667",
    average_after="3.33333334",
    cost_entry_id=200,
):
    return MovingAverageReplayMovement(
        movement_id=movement_id,
        movement_date=movement_date,
        movement_type=StockMovementType.ISSUE,
        quantity_delta=Decimal(
            quantity
        ),
        value_delta=Decimal(
            value
        ),
        balance_quantity_after=Decimal(
            balance_quantity_after
        ),
        balance_value_after=Decimal(
            balance_value_after
        ),
        average_unit_cost_after=Decimal(
            average_after
        ),
        inventory_cost_entry_id=cost_entry_id,
    )


def _calculate(
    peers,
):
    return (
        calculate_purchase_value_correction_moving_average_peer_replay(
            opening_quantity=Decimal("0"),
            opening_inventory_value=Decimal("0"),
            receipt_quantity=Decimal("3"),
            original_receipt_value=Decimal("10"),
            historical_receipt_balance_quantity_after=Decimal(
                "3"
            ),
            historical_receipt_balance_value_after=Decimal(
                "10"
            ),
            historical_receipt_average_unit_cost_after=Decimal(
                "3.33333333"
            ),
            later_movements=(
                _issue(),
            ),
            peers=tuple(
                peers
            ),
        )
    )


def test_two_peers_are_replayed_cumulatively_not_independently():
    result = _calculate(
        (
            _peer(
                10,
                "1",
            ),
            _peer(
                20,
                "1",
            ),
        )
    )

    assert (
        result.aggregate_receipt_value_delta
        == Decimal("2.00000000")
    )

    assert (
        result.final_replay_result
        .receipt_value_delta
        == Decimal("2.00000000")
    )

    assert (
        result.attributed_delta_total
        == Decimal("2.00000000")
    )

    assert len(
        result.steps
    ) == 2

    assert (
        result.steps[0]
        .cumulative_corrected_receipt_value
        == Decimal("11.00000000")
    )

    assert (
        result.steps[1]
        .cumulative_corrected_receipt_value
        == Decimal("12.00000000")
    )


def test_each_peer_conserves_its_own_delta():
    result = _calculate(
        (
            _peer(
                10,
                "1",
            ),
            _peer(
                20,
                "1",
            ),
        )
    )

    assert (
        result.steps[0]
        .attributed_delta_total
        == Decimal("1.00000000")
    )

    assert (
        result.steps[1]
        .attributed_delta_total
        == Decimal("1.00000000")
    )


def test_second_peer_is_marginal_from_first_cumulative_state():
    result = _calculate(
        (
            _peer(
                10,
                "1",
            ),
            _peer(
                20,
                "1",
            ),
        )
    )

    second = result.steps[1]

    by_kind = {
        impact.effect_kind: impact
        for impact in second.attributed_impacts
    }

    assert set(
        by_kind
    ) == {
        "issued",
        "on_hand",
    }

    assert (
        by_kind["issued"]
        .original_valuation_amount
        == Decimal("3.66666667")
    )

    assert (
        by_kind["issued"]
        .corrected_valuation_amount
        == Decimal("4.00000000")
    )

    assert (
        by_kind["issued"]
        .valuation_delta
        == Decimal("0.33333333")
    )

    assert (
        by_kind["on_hand"]
        .original_valuation_amount
        == Decimal("7.33333333")
    )

    assert (
        by_kind["on_hand"]
        .corrected_valuation_amount
        == Decimal("8.00000000")
    )

    assert (
        by_kind["on_hand"]
        .valuation_delta
        == Decimal("0.66666667")
    )


def test_peer_order_is_recognition_date_then_allocation_id():
    result = _calculate(
        (
            _peer(
                30,
                "0.25",
                recognition_date=date(
                    2026,
                    1,
                    7,
                ),
            ),
            _peer(
                20,
                "0.25",
                recognition_date=date(
                    2026,
                    1,
                    5,
                ),
            ),
            _peer(
                10,
                "0.25",
                recognition_date=date(
                    2026,
                    1,
                    5,
                ),
            ),
        )
    )

    assert tuple(
        peer.allocation_event_id
        for peer in result.ordered_peers
    ) == (
        10,
        20,
        30,
    )


def test_mixed_increase_and_decrease_peers_conserve():
    result = _calculate(
        (
            _peer(
                10,
                "2",
            ),
            _peer(
                20,
                "-0.75",
            ),
        )
    )

    assert (
        result.aggregate_receipt_value_delta
        == Decimal("1.25000000")
    )

    assert (
        result.steps[0]
        .attributed_delta_total
        == Decimal("2.00000000")
    )

    assert (
        result.steps[1]
        .attributed_delta_total
        == Decimal("-0.75000000")
    )

    assert (
        result.attributed_delta_total
        == Decimal("1.25000000")
    )


def test_negative_cumulative_receipt_value_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError
    ):
        _calculate(
            (
                _peer(
                    10,
                    "-11",
                ),
            )
        )


def test_duplicate_active_allocation_id_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError
    ):
        _calculate(
            (
                _peer(
                    10,
                    "1",
                ),
                _peer(
                    10,
                    "2",
                ),
            )
        )


def test_zero_delta_active_peer_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError
    ):
        _calculate(
            (
                _peer(
                    10,
                    "0",
                ),
            )
        )


def test_single_peer_matches_expected_aggregate_effect():
    result = _calculate(
        (
            _peer(
                10,
                "1",
            ),
        )
    )

    assert (
        result.aggregate_receipt_value_delta
        == Decimal("1.00000000")
    )

    assert (
        result.final_replay_result
        .impact_delta_total
        == Decimal("1.00000000")
    )

    assert (
        result.steps[0]
        .attributed_delta_total
        == Decimal("1.00000000")
    )


def test_peer_attribution_keeps_issue_provenance():
    result = _calculate(
        (
            _peer(
                10,
                "1",
            ),
        )
    )

    issued = next(
        impact
        for impact in result.steps[0].attributed_impacts
        if impact.effect_kind == "issued"
    )

    assert (
        issued.source_moving_average_movement_id
        == 2
    )

    assert (
        issued.source_inventory_cost_entry_id
        == 200
    )


def test_on_hand_peer_attribution_has_no_issue_provenance():
    result = _calculate(
        (
            _peer(
                10,
                "1",
            ),
        )
    )

    on_hand = next(
        impact
        for impact in result.steps[0].attributed_impacts
        if impact.effect_kind == "on_hand"
    )

    assert (
        on_hand.source_moving_average_movement_id
        is None
    )

    assert (
        on_hand.source_inventory_cost_entry_id
        is None
    )
