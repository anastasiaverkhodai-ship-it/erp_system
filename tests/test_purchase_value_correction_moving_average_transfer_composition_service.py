from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.stock_ledger import (
    StockMovementType,
)
from app.services.purchase_value_correction_moving_average_replay_calculation_service import (
    MovingAverageReplayImpact,
    MovingAverageReplayMovement,
    PurchaseValueCorrectionMovingAverageReplayResult,
)
from app.services.purchase_value_correction_moving_average_transfer_composition_service import (
    PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError,
    compose_purchase_value_correction_moving_average_transfer_replay,
)
from app.services.purchase_value_correction_moving_average_transfer_destination_replay_service import (
    PurchaseValueCorrectionMovingAverageTransferDestinationReplay,
    PurchaseValueCorrectionMovingAverageTransferDestinationReplaySource,
)
from app.services.purchase_value_correction_moving_average_transfer_routing_service import (
    PurchaseValueCorrectionMovingAverageTransferRoute,
)
import app.services.purchase_value_correction_moving_average_transfer_composition_service as service


D1 = date(2026, 1, 1)
D2 = date(2026, 1, 2)
D3 = date(2026, 1, 3)
D4 = date(2026, 1, 4)


def replay_movement(
    *,
    movement_id,
    movement_date,
    ice_id,
):
    return MovingAverageReplayMovement(
        movement_id=movement_id,
        movement_date=movement_date,
        movement_type=(
            StockMovementType.ISSUE
        ),
        quantity_delta=Decimal("-10"),
        value_delta=Decimal("-20"),
        balance_quantity_after=Decimal("90"),
        balance_value_after=Decimal("180"),
        average_unit_cost_after=Decimal("2"),
        inventory_cost_entry_id=ice_id,
    )


def impact(
    *,
    effect_kind,
    quantity,
    original,
    corrected,
    movement_id=None,
    ice_id=None,
):
    return MovingAverageReplayImpact(
        effect_kind=effect_kind,
        quantity=Decimal(quantity),
        original_valuation_amount=Decimal(
            original
        ),
        corrected_valuation_amount=Decimal(
            corrected
        ),
        source_moving_average_movement_id=(
            movement_id
        ),
        source_inventory_cost_entry_id=(
            ice_id
        ),
    )


def source(
    *,
    later_movements=(),
):
    return SimpleNamespace(
        company_id=1,
        product_id=7,
        warehouse_id=10,
        recognition_date=D1,
        later_movements=tuple(
            later_movements
        ),
    )


def result(
    *impacts,
    delta="-10",
):
    return PurchaseValueCorrectionMovingAverageReplayResult(
        receipt_value_delta=Decimal(
            delta
        ),
        impacts=tuple(
            impacts
        ),
        replay_states=(),
        final_quantity=Decimal("0"),
        original_final_value=Decimal("0"),
        corrected_final_value=Decimal("0"),
    )


def route(
    *,
    source_ice_id,
    destination_warehouse_id,
    layer_id,
    destination_ice_id,
):
    return (
        PurchaseValueCorrectionMovingAverageTransferRoute(
            warehouse_transfer_valuation_layer_id=(
                layer_id
            ),
            company_id=1,
            product_id=7,
            destination_warehouse_id=(
                destination_warehouse_id
            ),
            source_inventory_cost_entry_id=(
                source_ice_id
            ),
            destination_receipt_document_id=(
                1000 + layer_id
            ),
            destination_receipt_document_line_id=(
                2000 + layer_id
            ),
            destination_inventory_cost_entry_id=(
                destination_ice_id
            ),
            quantity=Decimal("10"),
            unit_cost=Decimal("2"),
            valuation_amount=Decimal("20"),
        )
    )


def destination_replay(
    *,
    source_ice_id,
    destination_warehouse_id,
    layer_id,
    destination_impacts,
    later_movements=(),
):
    destination_source = (
        PurchaseValueCorrectionMovingAverageTransferDestinationReplaySource(
            warehouse_transfer_valuation_layer_id=(
                layer_id
            ),
            source_inventory_cost_entry_id=(
                source_ice_id
            ),
            destination_inventory_cost_entry_id=(
                9000 + layer_id
            ),
            company_id=1,
            product_id=7,
            warehouse_id=(
                destination_warehouse_id
            ),
            destination_receipt_moving_average_movement_id=(
                3000 + layer_id
            ),
            opening_quantity=Decimal("0"),
            opening_inventory_value=Decimal("0"),
            receipt_quantity=Decimal("10"),
            original_receipt_value=Decimal("20"),
            corrected_receipt_value=Decimal("10"),
            historical_receipt_balance_quantity_after=Decimal(
                "10"
            ),
            historical_receipt_balance_value_after=Decimal(
                "20"
            ),
            historical_receipt_average_unit_cost_after=Decimal(
                "2"
            ),
            later_movements=tuple(
                later_movements
            ),
        )
    )

    destination_result = result(
        *destination_impacts,
        delta="-10",
    )

    return (
        PurchaseValueCorrectionMovingAverageTransferDestinationReplay(
            source=destination_source,
            replay_result=destination_result,
        )
    )


@pytest.mark.asyncio
async def test_non_transfer_issue_remains_source_warehouse(
    monkeypatch,
):
    source_issue = impact(
        effect_kind="issued",
        quantity="10",
        original="20",
        corrected="10",
        movement_id=11,
        ice_id=101,
    )

    async def resolve(
        db,
        *,
        company_id,
        source_inventory_cost_entry_id,
    ):
        return None

    monkeypatch.setattr(
        service,
        "resolve_moving_average_transfer_route",
        resolve,
    )

    composed = (
        await compose_purchase_value_correction_moving_average_transfer_replay(
            object(),
            source=source(
                later_movements=(
                    replay_movement(
                        movement_id=11,
                        movement_date=D2,
                        ice_id=101,
                    ),
                )
            ),
            source_replay_result=result(
                source_issue
            ),
        )
    )

    assert len(composed.impacts) == 1

    final = composed.impacts[0]

    assert final.warehouse_id == 10
    assert final.effect_kind == "issued"
    assert final.source_inventory_cost_entry_id == 101
    assert final.recognition_date == D2
    assert (
        final.warehouse_transfer_valuation_layer_ids
        == ()
    )


@pytest.mark.asyncio
async def test_transfer_issue_becomes_destination_on_hand(
    monkeypatch,
):
    source_issue = impact(
        effect_kind="issued",
        quantity="10",
        original="20",
        corrected="10",
        movement_id=11,
        ice_id=101,
    )

    route_1 = route(
        source_ice_id=101,
        destination_warehouse_id=20,
        layer_id=501,
        destination_ice_id=601,
    )

    async def resolve(
        db,
        *,
        company_id,
        source_inventory_cost_entry_id,
    ):
        if source_inventory_cost_entry_id == 101:
            return route_1

        return None

    async def destination(
        db,
        *,
        route,
        source_transfer_original_valuation_amount,
        source_transfer_corrected_valuation_amount,
    ):
        return destination_replay(
            source_ice_id=101,
            destination_warehouse_id=20,
            layer_id=501,
            destination_impacts=(
                impact(
                    effect_kind="on_hand",
                    quantity="10",
                    original="20",
                    corrected="10",
                ),
            ),
        )

    monkeypatch.setattr(
        service,
        "resolve_moving_average_transfer_route",
        resolve,
    )

    monkeypatch.setattr(
        service,
        (
            "load_and_calculate_moving_average_"
            "transfer_destination_replay"
        ),
        destination,
    )

    composed = (
        await compose_purchase_value_correction_moving_average_transfer_replay(
            object(),
            source=source(
                later_movements=(
                    replay_movement(
                        movement_id=11,
                        movement_date=D2,
                        ice_id=101,
                    ),
                )
            ),
            source_replay_result=result(
                source_issue
            ),
        )
    )

    assert len(composed.impacts) == 1

    final = composed.impacts[0]

    assert final.warehouse_id == 20
    assert final.effect_kind == "on_hand"
    assert final.recognition_date == D1

    assert (
        final.source_inventory_cost_entry_id
        is None
    )

    assert (
        final.warehouse_transfer_valuation_layer_ids
        == (501,)
    )

    assert composed.impact_delta_total == Decimal("-10")


@pytest.mark.asyncio
async def test_transfer_issue_becomes_destination_mixed(
    monkeypatch,
):
    source_issue = impact(
        effect_kind="issued",
        quantity="10",
        original="20",
        corrected="10",
        movement_id=11,
        ice_id=101,
    )

    route_1 = route(
        source_ice_id=101,
        destination_warehouse_id=20,
        layer_id=501,
        destination_ice_id=601,
    )

    destination_issue_movement = (
        replay_movement(
            movement_id=21,
            movement_date=D3,
            ice_id=201,
        )
    )

    async def resolve(
        db,
        *,
        company_id,
        source_inventory_cost_entry_id,
    ):
        if source_inventory_cost_entry_id == 101:
            return route_1

        return None

    async def destination(
        db,
        *,
        route,
        source_transfer_original_valuation_amount,
        source_transfer_corrected_valuation_amount,
    ):
        return destination_replay(
            source_ice_id=101,
            destination_warehouse_id=20,
            layer_id=501,
            destination_impacts=(
                impact(
                    effect_kind="issued",
                    quantity="4",
                    original="8",
                    corrected="4",
                    movement_id=21,
                    ice_id=201,
                ),
                impact(
                    effect_kind="on_hand",
                    quantity="6",
                    original="12",
                    corrected="6",
                ),
            ),
            later_movements=(
                destination_issue_movement,
            ),
        )

    monkeypatch.setattr(
        service,
        "resolve_moving_average_transfer_route",
        resolve,
    )

    monkeypatch.setattr(
        service,
        (
            "load_and_calculate_moving_average_"
            "transfer_destination_replay"
        ),
        destination,
    )

    composed = (
        await compose_purchase_value_correction_moving_average_transfer_replay(
            object(),
            source=source(
                later_movements=(
                    replay_movement(
                        movement_id=11,
                        movement_date=D2,
                        ice_id=101,
                    ),
                )
            ),
            source_replay_result=result(
                source_issue
            ),
        )
    )

    assert len(composed.impacts) == 2

    issued = next(
        value
        for value in composed.impacts
        if value.effect_kind == "issued"
    )

    on_hand = next(
        value
        for value in composed.impacts
        if value.effect_kind == "on_hand"
    )

    assert issued.warehouse_id == 20
    assert issued.source_inventory_cost_entry_id == 201
    assert issued.recognition_date == D3

    assert on_hand.warehouse_id == 20
    assert on_hand.recognition_date == D1

    assert composed.impact_delta_total == Decimal("-10")


@pytest.mark.asyncio
async def test_preserves_source_non_transfer_and_routes_transfer(
    monkeypatch,
):
    ordinary = impact(
        effect_kind="issued",
        quantity="5",
        original="10",
        corrected="8",
        movement_id=10,
        ice_id=100,
    )

    transfer = impact(
        effect_kind="issued",
        quantity="10",
        original="20",
        corrected="12",
        movement_id=11,
        ice_id=101,
    )

    route_1 = route(
        source_ice_id=101,
        destination_warehouse_id=20,
        layer_id=501,
        destination_ice_id=601,
    )

    async def resolve(
        db,
        *,
        company_id,
        source_inventory_cost_entry_id,
    ):
        if source_inventory_cost_entry_id == 101:
            return route_1

        return None

    async def destination(
        db,
        *,
        route,
        source_transfer_original_valuation_amount,
        source_transfer_corrected_valuation_amount,
    ):
        # transfer delta = -8
        return (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplay(
                source=(
                    PurchaseValueCorrectionMovingAverageTransferDestinationReplaySource(
                        warehouse_transfer_valuation_layer_id=501,
                        source_inventory_cost_entry_id=101,
                        destination_inventory_cost_entry_id=601,
                        company_id=1,
                        product_id=7,
                        warehouse_id=20,
                        destination_receipt_moving_average_movement_id=31,
                        opening_quantity=Decimal("0"),
                        opening_inventory_value=Decimal("0"),
                        receipt_quantity=Decimal("10"),
                        original_receipt_value=Decimal("20"),
                        corrected_receipt_value=Decimal("12"),
                        historical_receipt_balance_quantity_after=Decimal("10"),
                        historical_receipt_balance_value_after=Decimal("20"),
                        historical_receipt_average_unit_cost_after=Decimal("2"),
                        later_movements=(),
                    )
                ),
                replay_result=result(
                    impact(
                        effect_kind="on_hand",
                        quantity="10",
                        original="20",
                        corrected="12",
                    ),
                    delta="-8",
                ),
            )
        )

    monkeypatch.setattr(
        service,
        "resolve_moving_average_transfer_route",
        resolve,
    )

    monkeypatch.setattr(
        service,
        (
            "load_and_calculate_moving_average_"
            "transfer_destination_replay"
        ),
        destination,
    )

    source_result = result(
        ordinary,
        transfer,
        delta="-10",
    )

    composed = (
        await compose_purchase_value_correction_moving_average_transfer_replay(
            object(),
            source=source(
                later_movements=(
                    replay_movement(
                        movement_id=10,
                        movement_date=D2,
                        ice_id=100,
                    ),
                    replay_movement(
                        movement_id=11,
                        movement_date=D2,
                        ice_id=101,
                    ),
                )
            ),
            source_replay_result=source_result,
        )
    )

    assert len(composed.impacts) == 2

    source_final = next(
        value
        for value in composed.impacts
        if value.warehouse_id == 10
    )

    destination_final = next(
        value
        for value in composed.impacts
        if value.warehouse_id == 20
    )

    assert source_final.source_inventory_cost_entry_id == 100

    assert (
        destination_final
        .warehouse_transfer_valuation_layer_ids
        == (501,)
    )

    assert composed.impact_delta_total == Decimal("-10")


@pytest.mark.asyncio
async def test_supports_two_transfer_hops(
    monkeypatch,
):
    first_issue = impact(
        effect_kind="issued",
        quantity="10",
        original="20",
        corrected="10",
        movement_id=11,
        ice_id=101,
    )

    route_1 = route(
        source_ice_id=101,
        destination_warehouse_id=20,
        layer_id=501,
        destination_ice_id=601,
    )

    route_2 = route(
        source_ice_id=201,
        destination_warehouse_id=30,
        layer_id=502,
        destination_ice_id=602,
    )

    async def resolve(
        db,
        *,
        company_id,
        source_inventory_cost_entry_id,
    ):
        if source_inventory_cost_entry_id == 101:
            return route_1

        if source_inventory_cost_entry_id == 201:
            return route_2

        return None

    calls = []

    async def destination(
        db,
        *,
        route,
        source_transfer_original_valuation_amount,
        source_transfer_corrected_valuation_amount,
    ):
        calls.append(
            route.warehouse_transfer_valuation_layer_id
        )

        if (
            route.warehouse_transfer_valuation_layer_id
            == 501
        ):
            later = (
                replay_movement(
                    movement_id=21,
                    movement_date=D3,
                    ice_id=201,
                ),
            )

            return destination_replay(
                source_ice_id=101,
                destination_warehouse_id=20,
                layer_id=501,
                destination_impacts=(
                    impact(
                        effect_kind="issued",
                        quantity="10",
                        original="20",
                        corrected="10",
                        movement_id=21,
                        ice_id=201,
                    ),
                ),
                later_movements=later,
            )

        return destination_replay(
            source_ice_id=201,
            destination_warehouse_id=30,
            layer_id=502,
            destination_impacts=(
                impact(
                    effect_kind="on_hand",
                    quantity="10",
                    original="20",
                    corrected="10",
                ),
            ),
        )

    monkeypatch.setattr(
        service,
        "resolve_moving_average_transfer_route",
        resolve,
    )

    monkeypatch.setattr(
        service,
        (
            "load_and_calculate_moving_average_"
            "transfer_destination_replay"
        ),
        destination,
    )

    composed = (
        await compose_purchase_value_correction_moving_average_transfer_replay(
            object(),
            source=source(
                later_movements=(
                    replay_movement(
                        movement_id=11,
                        movement_date=D2,
                        ice_id=101,
                    ),
                )
            ),
            source_replay_result=result(
                first_issue
            ),
        )
    )

    assert calls == [
        501,
        502,
    ]

    assert len(composed.impacts) == 1

    final = composed.impacts[0]

    assert final.warehouse_id == 30

    assert (
        final.warehouse_transfer_valuation_layer_ids
        == (
            501,
            502,
        )
    )

    assert composed.impact_delta_total == Decimal("-10")


@pytest.mark.asyncio
async def test_cycle_is_rejected(
    monkeypatch,
):
    first_issue = impact(
        effect_kind="issued",
        quantity="10",
        original="20",
        corrected="10",
        movement_id=11,
        ice_id=101,
    )

    route_1 = route(
        source_ice_id=101,
        destination_warehouse_id=20,
        layer_id=501,
        destination_ice_id=601,
    )

    async def resolve(
        db,
        *,
        company_id,
        source_inventory_cost_entry_id,
    ):
        return route_1

    async def destination(
        db,
        *,
        route,
        source_transfer_original_valuation_amount,
        source_transfer_corrected_valuation_amount,
    ):
        return destination_replay(
            source_ice_id=101,
            destination_warehouse_id=20,
            layer_id=501,
            destination_impacts=(
                impact(
                    effect_kind="issued",
                    quantity="10",
                    original="20",
                    corrected="10",
                    movement_id=21,
                    ice_id=101,
                ),
            ),
            later_movements=(
                replay_movement(
                    movement_id=21,
                    movement_date=D3,
                    ice_id=101,
                ),
            ),
        )

    monkeypatch.setattr(
        service,
        "resolve_moving_average_transfer_route",
        resolve,
    )

    monkeypatch.setattr(
        service,
        (
            "load_and_calculate_moving_average_"
            "transfer_destination_replay"
        ),
        destination,
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError,
        match="cycle",
    ):
        await compose_purchase_value_correction_moving_average_transfer_replay(
            object(),
            source=source(
                later_movements=(
                    replay_movement(
                        movement_id=11,
                        movement_date=D2,
                        ice_id=101,
                    ),
                )
            ),
            source_replay_result=result(
                first_issue
            ),
        )
