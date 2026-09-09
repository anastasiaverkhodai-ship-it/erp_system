from datetime import date
from decimal import Decimal

import pytest

from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
)
from app.models.stock_ledger import (
    StockMovementType,
)
from app.services.purchase_value_correction_moving_average_replay_calculation_service import (
    MovingAverageReplayMovement,
    PurchaseValueCorrectionMovingAverageReplayError,
    calculate_purchase_value_correction_moving_average_replay,
)


def D(
    value,
):
    return Decimal(
        value
    )


def test_event_valuation_delta():
    event = PurchaseValueCorrectionMovingAverageReplayEvent(
        company_id=1,
        purchase_value_correction_allocation_event_id=10,
        product_id=20,
        warehouse_id=30,
        effect_kind="on_hand",
        source_moving_average_movement_id=None,
        source_inventory_cost_entry_id=None,
        recognition_date=date(
            2026,
            9,
            5,
        ),
        quantity=D(
            "60.0000"
        ),
        original_valuation_amount=D(
            "600.00000000"
        ),
        corrected_valuation_amount=D(
            "540.00000000"
        ),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=None,
    )

    assert (
        event.valuation_delta
        == D(
            "-60.00000000"
        )
    )


def test_receipt_1000_to_900_then_issue_40():
    result = calculate_purchase_value_correction_moving_average_replay(
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
            MovingAverageReplayMovement(
                movement_id=2,
                movement_date=date(
                    2026,
                    9,
                    3,
                ),
                movement_type=(
                    StockMovementType.ISSUE
                ),
                quantity_delta=D(
                    "-40"
                ),
                value_delta=D(
                    "-400"
                ),
                balance_quantity_after=D(
                    "60"
                ),
                balance_value_after=D(
                    "600"
                ),
                average_unit_cost_after=D(
                    "10"
                ),
                inventory_cost_entry_id=77,
            ),
        ),
    )

    assert (
        result.receipt_value_delta
        == D(
            "-100.00000000"
        )
    )

    assert len(
        result.impacts
    ) == 2

    issued = result.impacts[
        0
    ]
    on_hand = result.impacts[
        1
    ]

    assert (
        issued.effect_kind
        == "issued"
    )
    assert (
        issued.quantity
        == D(
            "40"
        )
    )
    assert (
        issued.original_valuation_amount
        == D(
            "400.00000000"
        )
    )
    assert (
        issued.corrected_valuation_amount
        == D(
            "360.00000000"
        )
    )
    assert (
        issued.valuation_delta
        == D(
            "-40.00000000"
        )
    )
    assert (
        issued.source_moving_average_movement_id
        == 2
    )
    assert (
        issued.source_inventory_cost_entry_id
        == 77
    )

    assert (
        on_hand.effect_kind
        == "on_hand"
    )
    assert (
        on_hand.quantity
        == D(
            "60"
        )
    )
    assert (
        on_hand.original_valuation_amount
        == D(
            "600.00000000"
        )
    )
    assert (
        on_hand.corrected_valuation_amount
        == D(
            "540.00000000"
        )
    )
    assert (
        on_hand.valuation_delta
        == D(
            "-60.00000000"
        )
    )

    assert (
        result.impact_delta_total
        == D(
            "-100.00000000"
        )
    )


def test_later_receipt_changes_average_before_issue():
    result = calculate_purchase_value_correction_moving_average_replay(
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
            MovingAverageReplayMovement(
                movement_id=2,
                movement_date=date(
                    2026,
                    9,
                    2,
                ),
                movement_type=(
                    StockMovementType.RECEIPT
                ),
                quantity_delta=D(
                    "100"
                ),
                value_delta=D(
                    "2000"
                ),
                balance_quantity_after=D(
                    "200"
                ),
                balance_value_after=D(
                    "3000"
                ),
                average_unit_cost_after=D(
                    "15"
                ),
            ),
            MovingAverageReplayMovement(
                movement_id=3,
                movement_date=date(
                    2026,
                    9,
                    3,
                ),
                movement_type=(
                    StockMovementType.ISSUE
                ),
                quantity_delta=D(
                    "-100"
                ),
                value_delta=D(
                    "-1500"
                ),
                balance_quantity_after=D(
                    "100"
                ),
                balance_value_after=D(
                    "1500"
                ),
                average_unit_cost_after=D(
                    "15"
                ),
                inventory_cost_entry_id=88,
            ),
        ),
    )

    assert len(
        result.impacts
    ) == 2

    issued = result.impacts[
        0
    ]
    on_hand = result.impacts[
        1
    ]

    assert (
        issued.original_valuation_amount
        == D(
            "1500.00000000"
        )
    )
    assert (
        issued.corrected_valuation_amount
        == D(
            "1450.00000000"
        )
    )
    assert (
        issued.valuation_delta
        == D(
            "-50.00000000"
        )
    )

    assert (
        on_hand.original_valuation_amount
        == D(
            "1500.00000000"
        )
    )
    assert (
        on_hand.corrected_valuation_amount
        == D(
            "1450.00000000"
        )
    )
    assert (
        on_hand.valuation_delta
        == D(
            "-50.00000000"
        )
    )

    assert (
        result.impact_delta_total
        == D(
            "-100.00000000"
        )
    )


def test_full_issue_absorbs_entire_delta():
    result = calculate_purchase_value_correction_moving_average_replay(
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
            MovingAverageReplayMovement(
                movement_id=2,
                movement_date=date(
                    2026,
                    9,
                    2,
                ),
                movement_type=(
                    StockMovementType.ISSUE
                ),
                quantity_delta=D(
                    "-100"
                ),
                value_delta=D(
                    "-1000"
                ),
                balance_quantity_after=D(
                    "0"
                ),
                balance_value_after=D(
                    "0"
                ),
                average_unit_cost_after=D(
                    "0"
                ),
                inventory_cost_entry_id=99,
            ),
        ),
    )

    assert len(
        result.impacts
    ) == 1

    assert (
        result.impacts[
            0
        ].effect_kind
        == "issued"
    )

    assert (
        result.impacts[
            0
        ].valuation_delta
        == D(
            "-100.00000000"
        )
    )

    assert (
        result.final_quantity
        == D(
            "0"
        )
    )

    assert (
        result.corrected_final_value
        == D(
            "0.00000000"
        )
    )


def test_price_increase_supported():
    result = calculate_purchase_value_correction_moving_average_replay(
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
            "1100"
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
            MovingAverageReplayMovement(
                movement_id=2,
                movement_date=date(
                    2026,
                    9,
                    2,
                ),
                movement_type=(
                    StockMovementType.ISSUE
                ),
                quantity_delta=D(
                    "-40"
                ),
                value_delta=D(
                    "-400"
                ),
                balance_quantity_after=D(
                    "60"
                ),
                balance_value_after=D(
                    "600"
                ),
                average_unit_cost_after=D(
                    "10"
                ),
                inventory_cost_entry_id=111,
            ),
        ),
    )

    assert (
        result.impact_delta_total
        == D(
            "100.00000000"
        )
    )

    assert (
        result.impacts[
            0
        ].valuation_delta
        == D(
            "40.00000000"
        )
    )

    assert (
        result.impacts[
            1
        ].valuation_delta
        == D(
            "60.00000000"
        )
    )


def test_historical_issue_cost_mismatch_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplayError,
        match="Historical ISSUE",
    ):
        calculate_purchase_value_correction_moving_average_replay(
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
                MovingAverageReplayMovement(
                    movement_id=2,
                    movement_date=date(
                        2026,
                        9,
                        2,
                    ),
                    movement_type=(
                        StockMovementType.ISSUE
                    ),
                    quantity_delta=D(
                        "-40"
                    ),
                    value_delta=D(
                        "-399"
                    ),
                    balance_quantity_after=D(
                        "60"
                    ),
                    balance_value_after=D(
                        "601"
                    ),
                    average_unit_cost_after=D(
                        "10"
                    ),
                    inventory_cost_entry_id=10,
                ),
            ),
        )


def test_historical_after_state_mismatch_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplayError,
        match="after-state mismatch",
    ):
        calculate_purchase_value_correction_moving_average_replay(
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
                MovingAverageReplayMovement(
                    movement_id=2,
                    movement_date=date(
                        2026,
                        9,
                        2,
                    ),
                    movement_type=(
                        StockMovementType.RECEIPT
                    ),
                    quantity_delta=D(
                        "10"
                    ),
                    value_delta=D(
                        "100"
                    ),
                    balance_quantity_after=D(
                        "111"
                    ),
                    balance_value_after=D(
                        "1100"
                    ),
                    average_unit_cost_after=D(
                        "10"
                    ),
                ),
            ),
        )


def test_adjustment_fails_closed_for_now():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplayError,
        match="ADJUSTMENT",
    ):
        calculate_purchase_value_correction_moving_average_replay(
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
                MovingAverageReplayMovement(
                    movement_id=2,
                    movement_date=date(
                        2026,
                        9,
                        2,
                    ),
                    movement_type=(
                        StockMovementType.ADJUSTMENT
                    ),
                    quantity_delta=D(
                        "0"
                    ),
                    value_delta=D(
                        "10"
                    ),
                    balance_quantity_after=D(
                        "100"
                    ),
                    balance_value_after=D(
                        "1010"
                    ),
                    average_unit_cost_after=D(
                        "10.1"
                    ),
                ),
            ),
        )


def test_reversal_rows_must_be_filtered():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplayError,
        match="REVERSED",
    ):
        calculate_purchase_value_correction_moving_average_replay(
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
                MovingAverageReplayMovement(
                    movement_id=2,
                    movement_date=date(
                        2026,
                        9,
                        2,
                    ),
                    movement_type=(
                        StockMovementType.REVERSAL
                    ),
                    quantity_delta=D(
                        "10"
                    ),
                    value_delta=D(
                        "100"
                    ),
                    balance_quantity_after=D(
                        "110"
                    ),
                    balance_value_after=D(
                        "1100"
                    ),
                    average_unit_cost_after=D(
                        "10"
                    ),
                ),
            ),
        )
