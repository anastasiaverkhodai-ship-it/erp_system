from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.purchase_value_correction_moving_average_peer_reconciliation_service import (
    _target_from_peer_impact,
)
from app.services.purchase_value_correction_moving_average_peer_replay_service import (
    MovingAveragePeerAttributedImpact,
)


def _source():
    return SimpleNamespace(
        product_id=10,
        warehouse_id=20,
        base_source=SimpleNamespace(
            currency_code="UAH",
            later_movements=(
                SimpleNamespace(
                    movement_id=700,
                    movement_date=date(
                        2026,
                        1,
                        8,
                    ),
                ),
            ),
        ),
    )


def test_on_hand_target_uses_peer_recognition_date():
    impact = MovingAveragePeerAttributedImpact(
        allocation_event_id=100,
        recognition_date=date(
            2026,
            1,
            5,
        ),
        effect_kind="on_hand",
        quantity=Decimal("60"),
        source_moving_average_movement_id=None,
        source_inventory_cost_entry_id=None,
        original_valuation_amount=Decimal("600"),
        corrected_valuation_amount=Decimal("540"),
    )

    target = _target_from_peer_impact(
        source=_source(),
        impact=impact,
    )

    assert target.recognition_date == date(
        2026,
        1,
        5,
    )

    assert (
        target.purchase_value_correction_allocation_event_id
        == 100
    )


def test_issued_target_uses_issue_date_when_later():
    impact = MovingAveragePeerAttributedImpact(
        allocation_event_id=100,
        recognition_date=date(
            2026,
            1,
            5,
        ),
        effect_kind="issued",
        quantity=Decimal("40"),
        source_moving_average_movement_id=700,
        source_inventory_cost_entry_id=900,
        original_valuation_amount=Decimal("400"),
        corrected_valuation_amount=Decimal("360"),
    )

    target = _target_from_peer_impact(
        source=_source(),
        impact=impact,
    )

    assert target.recognition_date == date(
        2026,
        1,
        8,
    )


def test_issued_target_uses_peer_date_when_later():
    impact = MovingAveragePeerAttributedImpact(
        allocation_event_id=100,
        recognition_date=date(
            2026,
            1,
            10,
        ),
        effect_kind="issued",
        quantity=Decimal("40"),
        source_moving_average_movement_id=700,
        source_inventory_cost_entry_id=900,
        original_valuation_amount=Decimal("400"),
        corrected_valuation_amount=Decimal("360"),
    )

    target = _target_from_peer_impact(
        source=_source(),
        impact=impact,
    )

    assert target.recognition_date == date(
        2026,
        1,
        10,
    )


def test_peer_target_preserves_marginal_amounts():
    impact = MovingAveragePeerAttributedImpact(
        allocation_event_id=101,
        recognition_date=date(
            2026,
            1,
            5,
        ),
        effect_kind="on_hand",
        quantity=Decimal("60"),
        source_moving_average_movement_id=None,
        source_inventory_cost_entry_id=None,
        original_valuation_amount=Decimal("540"),
        corrected_valuation_amount=Decimal("510"),
    )

    target = _target_from_peer_impact(
        source=_source(),
        impact=impact,
    )

    assert (
        target.original_valuation_amount
        == Decimal("540")
    )

    assert (
        target.corrected_valuation_amount
        == Decimal("510")
    )

    assert target.valuation_delta == Decimal("-30")


def test_issued_target_preserves_exact_provenance():
    impact = MovingAveragePeerAttributedImpact(
        allocation_event_id=101,
        recognition_date=date(
            2026,
            1,
            5,
        ),
        effect_kind="issued",
        quantity=Decimal("40"),
        source_moving_average_movement_id=700,
        source_inventory_cost_entry_id=900,
        original_valuation_amount=Decimal("380"),
        corrected_valuation_amount=Decimal("370"),
    )

    target = _target_from_peer_impact(
        source=_source(),
        impact=impact,
    )

    assert (
        target.source_moving_average_movement_id
        == 700
    )

    assert (
        target.source_inventory_cost_entry_id
        == 900
    )


def test_missing_issue_movement_fails_closed():
    impact = MovingAveragePeerAttributedImpact(
        allocation_event_id=101,
        recognition_date=date(
            2026,
            1,
            5,
        ),
        effect_kind="issued",
        quantity=Decimal("40"),
        source_moving_average_movement_id=999,
        source_inventory_cost_entry_id=900,
        original_valuation_amount=Decimal("380"),
        corrected_valuation_amount=Decimal("370"),
    )

    with pytest.raises(Exception):
        _target_from_peer_impact(
            source=_source(),
            impact=impact,
        )
