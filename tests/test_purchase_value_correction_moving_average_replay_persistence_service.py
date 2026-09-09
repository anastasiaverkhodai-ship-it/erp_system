from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.purchase_value_correction_moving_average_replay_persistence_service import (
    PurchaseValueCorrectionMovingAverageReplayChronologyError,
    PurchaseValueCorrectionMovingAverageReplayDataIntegrityError,
    PurchaseValueCorrectionMovingAverageReplayTarget,
    _active_originals,
    _event_matches_target,
    _new_reversal,
    _same_state_except_date,
    _validate_history,
    _validate_target_shape,
)


def D(
    value,
):
    return Decimal(
        value
    )


def event(
    *,
    id=1,
    recognition_date=date(
        2026,
        9,
        5,
    ),
    original="40",
    corrected="36",
    reversal_of_id=None,
    effect_kind="issued",
    movement_id=10,
    cost_id=20,
):
    return SimpleNamespace(
        id=id,
        company_id=1,
        purchase_value_correction_allocation_event_id=100,
        product_id=5,
        warehouse_id=7,
        effect_kind=effect_kind,
        source_moving_average_movement_id=(
            movement_id
        ),
        source_inventory_cost_entry_id=(
            cost_id
        ),
        recognition_date=recognition_date,
        quantity=D(
            "4"
        ),
        original_valuation_amount=D(
            original
        ),
        corrected_valuation_amount=D(
            corrected
        ),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=reversal_of_id,
    )


def target(
    *,
    recognition_date=date(
        2026,
        9,
        5,
    ),
    original="40",
    corrected="36",
    effect_kind="issued",
    movement_id=10,
    cost_id=20,
):
    return (
        PurchaseValueCorrectionMovingAverageReplayTarget(
            purchase_value_correction_allocation_event_id=100,
            product_id=5,
            warehouse_id=7,
            effect_kind=effect_kind,
            source_moving_average_movement_id=movement_id,
            source_inventory_cost_entry_id=cost_id,
            recognition_date=recognition_date,
            quantity=D(
                "4"
            ),
            original_valuation_amount=D(
                original
            ),
            corrected_valuation_amount=D(
                corrected
            ),
            currency_code="UAH",
        )
    )


def test_issued_target_shape():
    _validate_target_shape(
        target()
    )


def test_on_hand_must_not_have_issue_provenance():
    bad = target(
        effect_kind="on_hand",
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplayDataIntegrityError,
        match="on_hand",
    ):
        _validate_target_shape(
            bad
        )


def test_reversal_inverts_valuation_delta():
    original = event()

    reversal = _new_reversal(
        original=original,
        created_by=9,
        reversal_date=date(
            2026,
            9,
            8,
        ),
    )

    assert (
        reversal.original_valuation_amount
        == D(
            "36"
        )
    )

    assert (
        reversal.corrected_valuation_amount
        == D(
            "40"
        )
    )

    assert (
        reversal.reversal_of_id
        == 1
    )


def test_valid_original_reversal_graph():
    original = event()

    reversal = event(
        id=2,
        recognition_date=date(
            2026,
            9,
            8,
        ),
        original="36",
        corrected="40",
        reversal_of_id=1,
    )

    _validate_history(
        (
            original,
            reversal,
        )
    )

    assert (
        _active_originals(
            (
                original,
                reversal,
            )
        )
        == ()
    )


def test_multiple_reversal_graph_fails_closed():
    original = event()

    r1 = event(
        id=2,
        original="36",
        corrected="40",
        reversal_of_id=1,
    )

    r2 = event(
        id=3,
        original="36",
        corrected="40",
        reversal_of_id=1,
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplayDataIntegrityError,
        match="Multiple",
    ):
        _validate_history(
            (
                original,
                r1,
                r2,
            )
        )


def test_reversal_cannot_predate_original():
    original = event(
        recognition_date=date(
            2026,
            9,
            8,
        )
    )

    reversal = event(
        id=2,
        recognition_date=date(
            2026,
            9,
            7,
        ),
        original="36",
        corrected="40",
        reversal_of_id=1,
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplayChronologyError
    ):
        _validate_history(
            (
                original,
                reversal,
            )
        )


def test_exact_target_matches():
    current = event()

    assert _event_matches_target(
        event=current,
        target=target(),
    )


def test_same_state_except_date():
    current = event(
        recognition_date=date(
            2026,
            9,
            9,
        )
    )

    wanted = target(
        recognition_date=date(
            2026,
            9,
            5,
        )
    )

    assert _same_state_except_date(
        event=current,
        target=wanted,
    )

    assert not _event_matches_target(
        event=current,
        target=wanted,
    )
