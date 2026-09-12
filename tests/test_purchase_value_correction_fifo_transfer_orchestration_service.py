from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

import app.services.purchase_value_correction_fifo_transfer_orchestration_service as service

from app.services.purchase_value_correction_fifo_impact_calculation_service import (
    ActiveFifoConsumptionCandidate,
    FifoTransferRoutedDestinationSlice,
)
from app.services.purchase_value_correction_fifo_transfer_routing_service import (
    PurchaseValueCorrectionFifoTransferRoute,
)
from app.services.purchase_value_correction_fifo_transfer_topology_service import (
    PurchaseValueCorrectionFifoTransferDestinationTopology,
    PurchaseValueCorrectionFifoTransferIssuedSlice,
)


def _consumption(
    *,
    consumption_id=801,
    quantity=Decimal("5"),
):
    return ActiveFifoConsumptionCandidate(
        stock_lot_consumption_id=consumption_id,
        issue_document_id=901,
        issue_document_line_id=902,
        issue_event_date=date(2026, 1, 10),
        quantity=quantity,
    )


def _route(
    *,
    source_consumption_id=801,
):
    return PurchaseValueCorrectionFifoTransferRoute(
        warehouse_transfer_valuation_layer_id=501,
        company_id=1,
        transfer_line_id=601,
        product_id=10,
        destination_warehouse_id=20,
        source_inventory_cost_entry_id=701,
        source_stock_lot_consumption_id=(
            source_consumption_id
        ),
        destination_receipt_document_id=901,
        destination_receipt_document_line_id=902,
        quantity=Decimal("5"),
        unit_cost=Decimal("12.50000000"),
        valuation_amount=Decimal("62.50000000"),
    )


def _topology(
    *,
    source_consumption_id=801,
):
    route = _route(
        source_consumption_id=source_consumption_id
    )

    issued = (
        PurchaseValueCorrectionFifoTransferIssuedSlice(
            stock_lot_consumption_id=1101,
            issue_document_id=1201,
            issue_document_line_id=1202,
            issue_event_date=date(2026, 2, 10),
            quantity=Decimal("3"),
            unit_cost=Decimal("12.5000"),
        ),
    )

    return (
        PurchaseValueCorrectionFifoTransferDestinationTopology(
            route=route,
            destination_stock_lot_id=1001,
            original_quantity=Decimal("5"),
            on_hand_quantity=Decimal("2"),
            issued_quantity=Decimal("3"),
            classification="mixed",
            issued_slices=issued,
        )
    )


@pytest.mark.asyncio
async def test_preload_ignores_non_transfer_consumption(
    monkeypatch,
):
    calls = []

    async def fake_route_loader(
        db,
        *,
        company_id,
        stock_lot_consumption_id,
    ):
        calls.append(
            (
                company_id,
                stock_lot_consumption_id,
            )
        )
        return None

    monkeypatch.setattr(
        service,
        "load_fifo_transfer_route_for_consumption",
        fake_route_loader,
    )

    router = (
        await service.preload_purchase_value_correction_fifo_transfer_router(
            object(),
            company_id=1,
            active_consumptions=(
                _consumption(),
            ),
        )
    )

    assert calls == [
        (
            1,
            801,
        )
    ]

    assert router.transfer_consumption_ids == ()

    result = router.route(
        source_consumption=_consumption(),
        local_start=Decimal("0"),
        local_end=Decimal("5"),
        source_recognition_date=date(
            2026,
            2,
            5,
        ),
    )

    assert result is None


@pytest.mark.asyncio
async def test_preload_loads_route_and_destination_topology(
    monkeypatch,
):
    route_calls = []
    topology_calls = []

    async def fake_route_loader(
        db,
        *,
        company_id,
        stock_lot_consumption_id,
    ):
        route_calls.append(
            stock_lot_consumption_id
        )

        return _route(
            source_consumption_id=(
                stock_lot_consumption_id
            )
        )

    async def fake_topology_loader(
        db,
        *,
        route,
    ):
        topology_calls.append(
            route.source_stock_lot_consumption_id
        )

        return _topology(
            source_consumption_id=(
                route.source_stock_lot_consumption_id
            )
        )

    monkeypatch.setattr(
        service,
        "load_fifo_transfer_route_for_consumption",
        fake_route_loader,
    )

    monkeypatch.setattr(
        service,
        "load_fifo_transfer_destination_topology",
        fake_topology_loader,
    )

    router = (
        await service.preload_purchase_value_correction_fifo_transfer_router(
            object(),
            company_id=1,
            active_consumptions=(
                _consumption(),
            ),
        )
    )

    assert route_calls == [
        801
    ]

    assert topology_calls == [
        801
    ]

    assert router.transfer_consumption_ids == (
        801,
    )


@pytest.mark.asyncio
async def test_preloaded_router_maps_transfer_overlap_to_calc_dto(
    monkeypatch,
):
    async def fake_route_loader(
        db,
        *,
        company_id,
        stock_lot_consumption_id,
    ):
        return _route(
            source_consumption_id=(
                stock_lot_consumption_id
            )
        )

    async def fake_topology_loader(
        db,
        *,
        route,
    ):
        return _topology(
            source_consumption_id=(
                route.source_stock_lot_consumption_id
            )
        )

    monkeypatch.setattr(
        service,
        "load_fifo_transfer_route_for_consumption",
        fake_route_loader,
    )

    monkeypatch.setattr(
        service,
        "load_fifo_transfer_destination_topology",
        fake_topology_loader,
    )

    router = (
        await service.preload_purchase_value_correction_fifo_transfer_router(
            object(),
            company_id=1,
            active_consumptions=(
                _consumption(),
            ),
        )
    )

    result = router.route(
        source_consumption=_consumption(),
        local_start=Decimal("0"),
        local_end=Decimal("5"),
        source_recognition_date=date(
            2026,
            2,
            5,
        ),
    )

    assert result is not None
    assert len(result) == 2

    assert all(
        isinstance(
            row,
            FifoTransferRoutedDestinationSlice,
        )
        for row in result
    )

    issued, on_hand = result

    assert issued.stock_lot_id == 1001
    assert issued.destination_kind == "issued"
    assert issued.stock_lot_consumption_id == 1101
    assert issued.issue_document_id == 1201
    assert issued.issue_document_line_id == 1202
    assert issued.quantity == Decimal("3.0000")
    assert issued.recognition_date == date(
        2026,
        2,
        10,
    )

    assert on_hand.stock_lot_id == 1001
    assert on_hand.destination_kind == "on_hand"
    assert on_hand.stock_lot_consumption_id is None
    assert on_hand.issue_document_id is None
    assert on_hand.issue_document_line_id is None
    assert on_hand.quantity == Decimal("2.0000")
    assert on_hand.recognition_date == date(
        2026,
        2,
        5,
    )


@pytest.mark.asyncio
async def test_partial_transfer_overlap_is_routed_by_local_offsets(
    monkeypatch,
):
    async def fake_route_loader(
        db,
        *,
        company_id,
        stock_lot_consumption_id,
    ):
        return _route(
            source_consumption_id=(
                stock_lot_consumption_id
            )
        )

    async def fake_topology_loader(
        db,
        *,
        route,
    ):
        return _topology(
            source_consumption_id=(
                route.source_stock_lot_consumption_id
            )
        )

    monkeypatch.setattr(
        service,
        "load_fifo_transfer_route_for_consumption",
        fake_route_loader,
    )

    monkeypatch.setattr(
        service,
        "load_fifo_transfer_destination_topology",
        fake_topology_loader,
    )

    router = (
        await service.preload_purchase_value_correction_fifo_transfer_router(
            object(),
            company_id=1,
            active_consumptions=(
                _consumption(),
            ),
        )
    )

    # Destination topology:
    # [0,3) issued
    # [3,5) on hand
    #
    # Route only source-transfer local [2,4).
    result = router.route(
        source_consumption=_consumption(),
        local_start=Decimal("2"),
        local_end=Decimal("4"),
        source_recognition_date=date(
            2026,
            2,
            5,
        ),
    )

    assert result is not None
    assert len(result) == 2

    assert result[0].destination_kind == "issued"
    assert result[0].quantity == Decimal("1.0000")

    assert result[1].destination_kind == "on_hand"
    assert result[1].quantity == Decimal("1.0000")


@pytest.mark.asyncio
async def test_multiple_consumptions_preload_only_transfer_ones(
    monkeypatch,
):
    async def fake_route_loader(
        db,
        *,
        company_id,
        stock_lot_consumption_id,
    ):
        if stock_lot_consumption_id == 801:
            return _route(
                source_consumption_id=801
            )

        return None

    async def fake_topology_loader(
        db,
        *,
        route,
    ):
        return _topology(
            source_consumption_id=(
                route.source_stock_lot_consumption_id
            )
        )

    monkeypatch.setattr(
        service,
        "load_fifo_transfer_route_for_consumption",
        fake_route_loader,
    )

    monkeypatch.setattr(
        service,
        "load_fifo_transfer_destination_topology",
        fake_topology_loader,
    )

    router = (
        await service.preload_purchase_value_correction_fifo_transfer_router(
            object(),
            company_id=1,
            active_consumptions=(
                _consumption(
                    consumption_id=700,
                    quantity=Decimal("2"),
                ),
                _consumption(
                    consumption_id=801,
                    quantity=Decimal("5"),
                ),
            ),
        )
    )

    assert router.transfer_consumption_ids == (
        801,
    )

    assert (
        router.route(
            source_consumption=_consumption(
                consumption_id=700,
                quantity=Decimal("2"),
            ),
            local_start=Decimal("0"),
            local_end=Decimal("2"),
            source_recognition_date=date(
                2026,
                2,
                5,
            ),
        )
        is None
    )


@pytest.mark.asyncio
async def test_duplicate_active_consumption_ids_rejected():
    with pytest.raises(
        service.PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError,
        match="duplicate",
    ):
        await service.preload_purchase_value_correction_fifo_transfer_router(
            object(),
            company_id=1,
            active_consumptions=(
                _consumption(),
                _consumption(),
            ),
        )


@pytest.mark.asyncio
async def test_route_quantity_must_match_source_consumption_quantity(
    monkeypatch,
):
    async def fake_route_loader(
        db,
        *,
        company_id,
        stock_lot_consumption_id,
    ):
        route = _route(
            source_consumption_id=(
                stock_lot_consumption_id
            )
        )

        return replace(
            route,
            quantity=Decimal("4"),
        )

    monkeypatch.setattr(
        service,
        "load_fifo_transfer_route_for_consumption",
        fake_route_loader,
    )

    with pytest.raises(
        service.PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError,
        match="quantity",
    ):
        await service.preload_purchase_value_correction_fifo_transfer_router(
            object(),
            company_id=1,
            active_consumptions=(
                _consumption(
                    quantity=Decimal("5"),
                ),
            ),
        )
