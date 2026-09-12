from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.company import (
    InventoryValuationMethod,
)
from app.services import (
    purchase_value_correction_moving_average_replay_source_loader
    as replay_source_loader,
)
from app.services.purchase_value_correction_moving_average_transfer_destination_replay_service import (
    PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError,
    PurchaseValueCorrectionMovingAverageTransferDestinationReplayNotFoundError,
    build_moving_average_transfer_destination_replay_source,
    calculate_moving_average_transfer_destination_replay,
)
from app.services.purchase_value_correction_moving_average_transfer_routing_service import (
    PurchaseValueCorrectionMovingAverageTransferRoute,
)


D1 = date(
    2026,
    1,
    1,
)

D2 = date(
    2026,
    1,
    2,
)

D3 = date(
    2026,
    1,
    3,
)


def route(
    **overrides,
):
    values = {
        "warehouse_transfer_valuation_layer_id": 101,
        "company_id": 1,
        "product_id": 7,
        "destination_warehouse_id": 3,
        "source_inventory_cost_entry_id": 301,
        "destination_receipt_document_id": 401,
        "destination_receipt_document_line_id": 402,
        "destination_inventory_cost_entry_id": 501,
        "quantity": Decimal(
            "50.0000"
        ),
        "unit_cost": Decimal(
            "2.00000000"
        ),
        "valuation_amount": Decimal(
            "100.00000000"
        ),
    }

    values.update(
        overrides
    )

    return (
        PurchaseValueCorrectionMovingAverageTransferRoute(
            **values
        )
    )


def movement(
    *,
    movement_id,
    movement_date,
    movement_type,
    document_id,
    document_line_id,
    quantity_delta,
    value_delta,
    balance_quantity_after,
    balance_value_after,
    average_unit_cost_after,
    warehouse_id=3,
):
    return SimpleNamespace(
        id=movement_id,
        company_id=1,
        document_id=document_id,
        document_line_id=document_line_id,
        product_id=7,
        warehouse_id=warehouse_id,
        movement_type=movement_type,
        movement_date=movement_date,
        quantity_delta=Decimal(
            quantity_delta
        ),
        value_delta=Decimal(
            value_delta
        ),
        balance_quantity_after=Decimal(
            balance_quantity_after
        ),
        balance_value_after=Decimal(
            balance_value_after
        ),
        average_unit_cost_after=Decimal(
            average_unit_cost_after
        ),
        reversal_of_id=None,
    )


def ice(
    *,
    ice_id,
    document_id,
    document_line_id,
):
    return SimpleNamespace(
        id=ice_id,
        company_id=1,
        document_id=document_id,
        document_line_id=document_line_id,
        valuation_method=(
            InventoryValuationMethod
            .WEIGHTED_AVERAGE_MOVING
        ),
    )


def destination_receipt():
    return movement(
        movement_id=20,
        movement_date=D2,
        movement_type=(
            replay_source_loader
            .StockMovementType
            .RECEIPT
        ),
        document_id=401,
        document_line_id=402,
        quantity_delta="50",
        value_delta="100",
        balance_quantity_after="50",
        balance_value_after="100",
        average_unit_cost_after="2",
    )


def build(
    *,
    rows=None,
    costs=None,
    original="100",
    corrected="90",
    route_row=None,
):
    return (
        build_moving_average_transfer_destination_replay_source(
            route=route_row or route(),
            source_transfer_original_valuation_amount=Decimal(
                original
            ),
            source_transfer_corrected_valuation_amount=Decimal(
                corrected
            ),
            stream_movements=tuple(
                rows
                if rows is not None
                else (
                    destination_receipt(),
                )
            ),
            inventory_cost_entries_by_document_line_id=(
                costs or {}
            ),
        )
    )


def test_builds_on_hand_destination_source():
    source = build()

    assert (
        source.warehouse_id
        == 3
    )

    assert (
        source.receipt_quantity
        == Decimal(
            "50"
        )
    )

    assert (
        source.original_receipt_value
        == Decimal(
            "100"
        )
    )

    assert (
        source.corrected_receipt_value
        == Decimal(
            "90"
        )
    )

    assert (
        source.routed_valuation_delta
        == Decimal(
            "-10"
        )
    )

    assert (
        source.later_movements
        == ()
    )


def test_existing_calculator_produces_destination_on_hand():
    composed = (
        calculate_moving_average_transfer_destination_replay(
            build()
        )
    )

    assert (
        composed
        .replay_result
        .receipt_value_delta
        == Decimal(
            "-10.00000000"
        )
    )

    assert (
        len(
            composed
            .replay_result
            .impacts
        )
        == 1
    )

    impact = (
        composed
        .replay_result
        .impacts[0]
    )

    assert (
        impact.effect_kind
        == "on_hand"
    )

    assert (
        impact.quantity
        == Decimal(
            "50.0000"
        )
    )

    assert (
        impact.original_valuation_amount
        == Decimal(
            "100.00000000"
        )
    )

    assert (
        impact.corrected_valuation_amount
        == Decimal(
            "90.00000000"
        )
    )

    assert (
        impact.source_inventory_cost_entry_id
        is None
    )


def test_destination_issue_is_replayed_not_source_transfer_issue():
    downstream_issue = movement(
        movement_id=21,
        movement_date=D3,
        movement_type=(
            replay_source_loader
            .StockMovementType
            .ISSUE
        ),
        document_id=601,
        document_line_id=602,
        quantity_delta="-20",
        value_delta="-40",
        balance_quantity_after="30",
        balance_value_after="60",
        average_unit_cost_after="2",
    )

    source = build(
        rows=(
            destination_receipt(),
            downstream_issue,
        ),
        costs={
            602: ice(
                ice_id=701,
                document_id=601,
                document_line_id=602,
            ),
        },
    )

    result = (
        calculate_moving_average_transfer_destination_replay(
            source
        )
        .replay_result
    )

    issued = tuple(
        impact
        for impact in result.impacts
        if impact.effect_kind
        == "issued"
    )

    on_hand = tuple(
        impact
        for impact in result.impacts
        if impact.effect_kind
        == "on_hand"
    )

    assert len(issued) == 1
    assert len(on_hand) == 1

    assert (
        issued[0].quantity
        == Decimal(
            "20.0000"
        )
    )

    assert (
        issued[0].source_inventory_cost_entry_id
        == 701
    )

    assert (
        issued[0].source_inventory_cost_entry_id
        != route().source_inventory_cost_entry_id
    )

    assert (
        on_hand[0].quantity
        == Decimal(
            "30.0000"
        )
    )

    assert (
        sum(
            (
                impact.valuation_delta
                for impact in result.impacts
            ),
            Decimal("0"),
        )
        == Decimal(
            "-10.00000000"
        )
    )


def test_destination_preexisting_balance_is_respected():
    preceding = movement(
        movement_id=10,
        movement_date=D1,
        movement_type=(
            replay_source_loader
            .StockMovementType
            .RECEIPT
        ),
        document_id=300,
        document_line_id=301,
        quantity_delta="50",
        value_delta="150",
        balance_quantity_after="50",
        balance_value_after="150",
        average_unit_cost_after="3",
    )

    receipt = movement(
        movement_id=20,
        movement_date=D2,
        movement_type=(
            replay_source_loader
            .StockMovementType
            .RECEIPT
        ),
        document_id=401,
        document_line_id=402,
        quantity_delta="50",
        value_delta="100",
        balance_quantity_after="100",
        balance_value_after="250",
        average_unit_cost_after="2.5",
    )

    source = build(
        rows=(
            preceding,
            receipt,
        )
    )

    assert (
        source.opening_quantity
        == Decimal(
            "50"
        )
    )

    assert (
        source.opening_inventory_value
        == Decimal(
            "150"
        )
    )

    result = (
        calculate_moving_average_transfer_destination_replay(
            source
        )
        .replay_result
    )

    assert (
        result.final_quantity
        == Decimal(
            "100.0000"
        )
    )

    assert (
        result.receipt_value_delta
        == Decimal(
            "-10.00000000"
        )
    )


def test_destination_later_receipt_uses_existing_replay_semantics():
    later_receipt = movement(
        movement_id=21,
        movement_date=D3,
        movement_type=(
            replay_source_loader
            .StockMovementType
            .RECEIPT
        ),
        document_id=601,
        document_line_id=602,
        quantity_delta="50",
        value_delta="150",
        balance_quantity_after="100",
        balance_value_after="250",
        average_unit_cost_after="2.5",
    )

    source = build(
        rows=(
            destination_receipt(),
            later_receipt,
        )
    )

    result = (
        calculate_moving_average_transfer_destination_replay(
            source
        )
        .replay_result
    )

    assert (
        result.receipt_value_delta
        == Decimal(
            "-10.00000000"
        )
    )

    assert (
        len(result.impacts)
        == 1
    )

    assert (
        result.impacts[0].effect_kind
        == "on_hand"
    )


def test_rejects_wrong_destination_stream():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError,
        match="another stream",
    ):
        build(
            rows=(
                movement(
                    movement_id=20,
                    movement_date=D2,
                    movement_type=(
                        replay_source_loader
                        .StockMovementType
                        .RECEIPT
                    ),
                    document_id=401,
                    document_line_id=402,
                    quantity_delta="50",
                    value_delta="100",
                    balance_quantity_after="50",
                    balance_value_after="100",
                    average_unit_cost_after="2",
                    warehouse_id=999,
                ),
            )
        )


def test_rejects_missing_destination_receipt():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferDestinationReplayNotFoundError,
        match="RECEIPT movement",
    ):
        build(
            rows=(
                movement(
                    movement_id=20,
                    movement_date=D2,
                    movement_type=(
                        replay_source_loader
                        .StockMovementType
                        .RECEIPT
                    ),
                    document_id=999,
                    document_line_id=999,
                    quantity_delta="50",
                    value_delta="100",
                    balance_quantity_after="50",
                    balance_value_after="100",
                    average_unit_cost_after="2",
                ),
            )
        )


def test_rejects_receipt_quantity_mismatch():
    bad = destination_receipt()
    bad.quantity_delta = Decimal(
        "49"
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError,
        match="quantity does not match",
    ):
        build(
            rows=(
                bad,
            )
        )


def test_rejects_wtvl_q8_value_mismatch():
    bad = destination_receipt()
    bad.value_delta = Decimal(
        "99.99999999"
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError,
        match="Q8 valuation",
    ):
        build(
            rows=(
                bad,
            )
        )


def test_rejects_source_issue_destination_receipt_value_break():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError,
        match="original valuation",
    ):
        build(
            original="99"
        )


def test_rejects_zero_routed_delta():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError,
        match="cannot be zero",
    ):
        build(
            corrected="100"
        )


def test_downstream_issue_requires_exact_ice():
    downstream_issue = movement(
        movement_id=21,
        movement_date=D3,
        movement_type=(
            replay_source_loader
            .StockMovementType
            .ISSUE
        ),
        document_id=601,
        document_line_id=602,
        quantity_delta="-20",
        value_delta="-40",
        balance_quantity_after="30",
        balance_value_after="60",
        average_unit_cost_after="2",
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferDestinationReplayNotFoundError,
        match="has no InventoryCostEntry",
    ):
        build(
            rows=(
                destination_receipt(),
                downstream_issue,
            )
        )


def test_composed_source_is_immutable():
    source = build()

    with pytest.raises(
        AttributeError
    ):
        source.receipt_quantity = Decimal(
            "1"
        )
