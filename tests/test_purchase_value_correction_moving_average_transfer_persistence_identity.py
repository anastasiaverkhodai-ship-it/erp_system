from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.purchase_value_correction_moving_average_replay_persistence_service import (
    PurchaseValueCorrectionMovingAverageReplayTarget,
    _event_key,
    _target_key,
)


D1 = date(
    2026,
    1,
    1,
)


def target(
    *,
    warehouse_id,
    effect_kind="on_hand",
    movement_id=None,
    ice_id=None,
):
    return (
        PurchaseValueCorrectionMovingAverageReplayTarget(
            purchase_value_correction_allocation_event_id=1,
            product_id=7,
            warehouse_id=warehouse_id,
            effect_kind=effect_kind,
            source_moving_average_movement_id=movement_id,
            source_inventory_cost_entry_id=ice_id,
            recognition_date=D1,
            quantity=Decimal("10"),
            original_valuation_amount=Decimal("20"),
            corrected_valuation_amount=Decimal("18"),
            currency_code="UAH",
        )
    )


def event(
    *,
    warehouse_id,
    effect_kind="on_hand",
    movement_id=None,
    ice_id=None,
):
    return SimpleNamespace(
        warehouse_id=warehouse_id,
        effect_kind=effect_kind,
        source_moving_average_movement_id=movement_id,
        source_inventory_cost_entry_id=ice_id,
    )


def test_on_hand_identity_distinguishes_warehouses():
    first = target(
        warehouse_id=10,
    )

    second = target(
        warehouse_id=20,
    )

    assert (
        _target_key(first)
        != _target_key(second)
    )

    assert _target_key(first) == (
        10,
        "on_hand",
        None,
        None,
    )

    assert _target_key(second) == (
        20,
        "on_hand",
        None,
        None,
    )


def test_issued_identity_distinguishes_warehouses():
    first = target(
        warehouse_id=10,
        effect_kind="issued",
        movement_id=101,
        ice_id=201,
    )

    second = target(
        warehouse_id=20,
        effect_kind="issued",
        movement_id=101,
        ice_id=201,
    )

    assert (
        _target_key(first)
        != _target_key(second)
    )


def test_target_and_event_identity_are_symmetric():
    value_target = target(
        warehouse_id=20,
        effect_kind="issued",
        movement_id=101,
        ice_id=201,
    )

    value_event = event(
        warehouse_id=20,
        effect_kind="issued",
        movement_id=101,
        ice_id=201,
    )

    assert (
        _target_key(
            value_target
        )
        == _event_key(
            value_event
        )
    )


def test_same_warehouse_same_provenance_keeps_same_identity():
    first = target(
        warehouse_id=20,
        effect_kind="issued",
        movement_id=101,
        ice_id=201,
    )

    second = target(
        warehouse_id=20,
        effect_kind="issued",
        movement_id=101,
        ice_id=201,
    )

    assert (
        _target_key(first)
        == _target_key(second)
    )


def test_event_on_hand_identity_distinguishes_warehouses():
    first = event(
        warehouse_id=10,
    )

    second = event(
        warehouse_id=20,
    )

    assert (
        _event_key(first)
        != _event_key(second)
    )
