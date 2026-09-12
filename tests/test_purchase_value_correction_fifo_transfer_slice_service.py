from datetime import date
from decimal import Decimal

import pytest

from app.services.purchase_value_correction_fifo_transfer_routing_service import (
    PurchaseValueCorrectionFifoTransferRoute,
)
from app.services.purchase_value_correction_fifo_transfer_topology_service import (
    PurchaseValueCorrectionFifoTransferDestinationTopology,
    PurchaseValueCorrectionFifoTransferIssuedSlice,
)
from app.services.purchase_value_correction_fifo_transfer_slice_service import (
    PurchaseValueCorrectionFifoTransferSliceIntegrityError,
    build_fifo_transfer_destination_slices,
)


def _route():
    return PurchaseValueCorrectionFifoTransferRoute(
        warehouse_transfer_valuation_layer_id=501,
        company_id=1,
        transfer_line_id=601,
        product_id=10,
        destination_warehouse_id=20,
        source_inventory_cost_entry_id=701,
        source_stock_lot_consumption_id=801,
        destination_receipt_document_id=901,
        destination_receipt_document_line_id=902,
        quantity=Decimal("5.0000"),
        unit_cost=Decimal("12.50000000"),
        valuation_amount=Decimal("62.50000000"),
    )


def _issued(
    *,
    consumption_id,
    document_id,
    line_id,
    event_date,
    quantity,
):
    return PurchaseValueCorrectionFifoTransferIssuedSlice(
        stock_lot_consumption_id=consumption_id,
        issue_document_id=document_id,
        issue_document_line_id=line_id,
        issue_event_date=event_date,
        quantity=quantity,
        unit_cost=Decimal("12.5000"),
    )


def _topology(
    *,
    on_hand,
    issued,
):
    issued = tuple(
        issued
    )

    issued_quantity = sum(
        (
            item.quantity
            for item in issued
        ),
        Decimal("0"),
    )

    if issued_quantity == 0:
        classification = "on_hand"
    elif on_hand == 0:
        classification = "issued"
    else:
        classification = "mixed"

    return PurchaseValueCorrectionFifoTransferDestinationTopology(
        route=_route(),
        destination_stock_lot_id=1001,
        original_quantity=Decimal("5.0000"),
        on_hand_quantity=on_hand,
        issued_quantity=issued_quantity,
        classification=classification,
        issued_slices=issued,
    )


def test_full_transfer_interval_maps_to_whole_destination():
    topology = _topology(
        on_hand=Decimal("2.0000"),
        issued=(
            _issued(
                consumption_id=1101,
                document_id=1201,
                line_id=1202,
                event_date=date(2026, 2, 10),
                quantity=Decimal("3.0000"),
            ),
        ),
    )

    result = build_fifo_transfer_destination_slices(
        topology=topology,
        local_start=Decimal("0.0000"),
        local_end=Decimal("5.0000"),
        source_recognition_date=date(2026, 2, 5),
    )

    assert len(result) == 2

    assert result[0].destination_kind == "issued"
    assert result[0].quantity == Decimal("3.0000")
    assert result[0].stock_lot_consumption_id == 1101
    assert result[0].recognition_date == date(
        2026,
        2,
        10,
    )

    assert result[1].destination_kind == "on_hand"
    assert result[1].quantity == Decimal("2.0000")
    assert result[1].stock_lot_consumption_id is None
    assert result[1].recognition_date == date(
        2026,
        2,
        5,
    )


def test_partial_interval_inside_first_downstream_issue():
    topology = _topology(
        on_hand=Decimal("2.0000"),
        issued=(
            _issued(
                consumption_id=1101,
                document_id=1201,
                line_id=1202,
                event_date=date(2026, 2, 10),
                quantity=Decimal("3.0000"),
            ),
        ),
    )

    result = build_fifo_transfer_destination_slices(
        topology=topology,
        local_start=Decimal("1.0000"),
        local_end=Decimal("2.5000"),
        source_recognition_date=date(2026, 2, 5),
    )

    assert len(result) == 1
    assert result[0].destination_kind == "issued"
    assert result[0].quantity == Decimal("1.5000")
    assert result[0].stock_lot_consumption_id == 1101


def test_partial_interval_crosses_issued_to_on_hand_boundary():
    topology = _topology(
        on_hand=Decimal("2.0000"),
        issued=(
            _issued(
                consumption_id=1101,
                document_id=1201,
                line_id=1202,
                event_date=date(2026, 2, 10),
                quantity=Decimal("3.0000"),
            ),
        ),
    )

    result = build_fifo_transfer_destination_slices(
        topology=topology,
        local_start=Decimal("2.0000"),
        local_end=Decimal("4.0000"),
        source_recognition_date=date(2026, 2, 5),
    )

    assert len(result) == 2

    assert result[0].destination_kind == "issued"
    assert result[0].quantity == Decimal("1.0000")

    assert result[1].destination_kind == "on_hand"
    assert result[1].quantity == Decimal("1.0000")


def test_partial_interval_can_hit_second_downstream_issue_only():
    topology = _topology(
        on_hand=Decimal("0.0000"),
        issued=(
            _issued(
                consumption_id=1101,
                document_id=1201,
                line_id=1202,
                event_date=date(2026, 2, 10),
                quantity=Decimal("2.0000"),
            ),
            _issued(
                consumption_id=1102,
                document_id=1301,
                line_id=1302,
                event_date=date(2026, 2, 12),
                quantity=Decimal("3.0000"),
            ),
        ),
    )

    result = build_fifo_transfer_destination_slices(
        topology=topology,
        local_start=Decimal("3.0000"),
        local_end=Decimal("5.0000"),
        source_recognition_date=date(2026, 2, 5),
    )

    assert len(result) == 1
    assert result[0].destination_kind == "issued"
    assert result[0].quantity == Decimal("2.0000")
    assert result[0].stock_lot_consumption_id == 1102
    assert result[0].recognition_date == date(
        2026,
        2,
        12,
    )


def test_source_recognition_date_wins_when_later():
    topology = _topology(
        on_hand=Decimal("0.0000"),
        issued=(
            _issued(
                consumption_id=1101,
                document_id=1201,
                line_id=1202,
                event_date=date(2026, 2, 10),
                quantity=Decimal("5.0000"),
            ),
        ),
    )

    result = build_fifo_transfer_destination_slices(
        topology=topology,
        local_start=Decimal("0.0000"),
        local_end=Decimal("1.0000"),
        source_recognition_date=date(2026, 2, 20),
    )

    assert result[0].recognition_date == date(
        2026,
        2,
        20,
    )


@pytest.mark.parametrize(
    ("start", "end"),
    (
        (
            Decimal("-1.0000"),
            Decimal("1.0000"),
        ),
        (
            Decimal("0.0000"),
            Decimal("0.0000"),
        ),
        (
            Decimal("4.0000"),
            Decimal("3.0000"),
        ),
        (
            Decimal("0.0000"),
            Decimal("6.0000"),
        ),
    ),
)
def test_invalid_local_interval_rejected(
    start,
    end,
):
    topology = _topology(
        on_hand=Decimal("5.0000"),
        issued=(),
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoTransferSliceIntegrityError,
        match="interval",
    ):
        build_fifo_transfer_destination_slices(
            topology=topology,
            local_start=start,
            local_end=end,
            source_recognition_date=date(
                2026,
                2,
                5,
            ),
        )


def test_slice_quantity_conservation():
    topology = _topology(
        on_hand=Decimal("1.0000"),
        issued=(
            _issued(
                consumption_id=1101,
                document_id=1201,
                line_id=1202,
                event_date=date(2026, 2, 10),
                quantity=Decimal("1.5000"),
            ),
            _issued(
                consumption_id=1102,
                document_id=1301,
                line_id=1302,
                event_date=date(2026, 2, 12),
                quantity=Decimal("2.5000"),
            ),
        ),
    )

    result = build_fifo_transfer_destination_slices(
        topology=topology,
        local_start=Decimal("0.7500"),
        local_end=Decimal("4.2500"),
        source_recognition_date=date(2026, 2, 5),
    )

    assert sum(
        (
            item.quantity
            for item in result
        ),
        Decimal("0"),
    ) == Decimal("3.5000")
