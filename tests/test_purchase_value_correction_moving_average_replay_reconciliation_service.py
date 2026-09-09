from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.purchase_value_correction_moving_average_replay_calculation_service import (
    MovingAverageReplayImpact,
    PurchaseValueCorrectionMovingAverageReplayResult,
)
from app.services.purchase_value_correction_moving_average_replay_reconciliation_service import (
    _build_desired_targets,
)
from app.services.purchase_value_correction_moving_average_replay_source_loader import (
    PurchaseValueCorrectionMovingAverageReplaySource,
)


def D(
    value,
):
    return Decimal(
        value
    )


def source():
    return (
        PurchaseValueCorrectionMovingAverageReplaySource(
            company_id=1,
            purchase_value_correction_allocation_event_id=100,
            invoice_fulfillment_allocation_id=200,
            fulfillment_line_id=300,
            source_receipt_moving_average_movement_id=1,
            product_id=5,
            warehouse_id=7,
            recognition_date=date(
                2026,
                9,
                5,
            ),
            currency_code="UAH",
            opening_quantity=D(
                "0"
            ),
            opening_inventory_value=D(
                "0"
            ),
            receipt_quantity=D(
                "100"
            ),
            original_receipt_value=D(
                "1000"
            ),
            corrected_receipt_value=D(
                "900"
            ),
            historical_receipt_balance_quantity_after=D(
                "100"
            ),
            historical_receipt_balance_value_after=D(
                "1000"
            ),
            historical_receipt_average_unit_cost_after=D(
                "10"
            ),
            later_movements=(
                SimpleNamespace(
                    movement_id=2,
                    movement_date=date(
                        2026,
                        9,
                        3,
                    ),
                ),
                SimpleNamespace(
                    movement_id=3,
                    movement_date=date(
                        2026,
                        9,
                        8,
                    ),
                ),
            ),
        )
    )


def replay_result(
    impacts,
):
    return (
        PurchaseValueCorrectionMovingAverageReplayResult(
            receipt_value_delta=D(
                "-100"
            ),
            impacts=tuple(
                impacts
            ),
            replay_states=(),
            final_quantity=D(
                "60"
            ),
            original_final_value=D(
                "600"
            ),
            corrected_final_value=D(
                "540"
            ),
        )
    )


def test_new_issued_before_pvca_uses_pvca_date():
    result = replay_result(
        (
            MovingAverageReplayImpact(
                effect_kind="issued",
                quantity=D(
                    "40"
                ),
                original_valuation_amount=D(
                    "400"
                ),
                corrected_valuation_amount=D(
                    "360"
                ),
                source_moving_average_movement_id=2,
                source_inventory_cost_entry_id=20,
            ),
        )
    )

    targets = _build_desired_targets(
        source=source(),
        replay_result=result,
    )

    assert (
        targets[0].recognition_date
        == date(
            2026,
            9,
            5,
        )
    )


def test_new_issued_after_pvca_uses_issue_date():
    result = replay_result(
        (
            MovingAverageReplayImpact(
                effect_kind="issued",
                quantity=D(
                    "10"
                ),
                original_valuation_amount=D(
                    "100"
                ),
                corrected_valuation_amount=D(
                    "90"
                ),
                source_moving_average_movement_id=3,
                source_inventory_cost_entry_id=30,
            ),
        )
    )

    targets = _build_desired_targets(
        source=source(),
        replay_result=result,
    )

    assert (
        targets[0].recognition_date
        == date(
            2026,
            9,
            8,
        )
    )


def test_new_on_hand_uses_pvca_date():
    result = replay_result(
        (
            MovingAverageReplayImpact(
                effect_kind="on_hand",
                quantity=D(
                    "60"
                ),
                original_valuation_amount=D(
                    "600"
                ),
                corrected_valuation_amount=D(
                    "540"
                ),
                source_moving_average_movement_id=None,
                source_inventory_cost_entry_id=None,
            ),
        )
    )

    targets = _build_desired_targets(
        source=source(),
        replay_result=result,
    )

    assert (
        targets[0].recognition_date
        == date(
            2026,
            9,
            5,
        )
    )

    assert (
        targets[0]
        .source_moving_average_movement_id
        is None
    )


def test_duplicate_destination_key_fails_closed():
    duplicate = (
        MovingAverageReplayImpact(
            effect_kind="on_hand",
            quantity=D(
                "60"
            ),
            original_valuation_amount=D(
                "600"
            ),
            corrected_valuation_amount=D(
                "540"
            ),
            source_moving_average_movement_id=None,
            source_inventory_cost_entry_id=None,
        )
    )

    with pytest.raises(
        Exception,
        match="duplicate",
    ):
        _build_desired_targets(
            source=source(),
            replay_result=replay_result(
                (
                    duplicate,
                    duplicate,
                )
            ),
        )
