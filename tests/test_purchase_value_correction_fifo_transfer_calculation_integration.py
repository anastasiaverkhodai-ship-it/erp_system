from datetime import date
from decimal import Decimal

from app.services.purchase_value_correction_fifo_impact_calculation_service import (
    ActiveFifoAllocationPeerCandidate,
    ActiveFifoConsumptionCandidate,
    FifoTransferRoutedDestinationSlice,
    PurchaseValueCorrectionFifoAllocationCandidate,
    build_purchase_value_correction_fifo_impact_targets,
)


def _peer():
    return ActiveFifoAllocationPeerCandidate(
        invoice_fulfillment_allocation_id=11,
        receipt_event_date=date(2026, 1, 1),
        quantity=Decimal("5"),
    )


def _correction():
    return PurchaseValueCorrectionFifoAllocationCandidate(
        purchase_value_correction_allocation_event_id=21,
        invoice_fulfillment_allocation_id=11,
        recognition_date=date(2026, 2, 5),
        quantity=Decimal("5"),
        original_allocated_base_amount=Decimal("50.00"),
        corrected_allocated_base_amount=Decimal("60.00"),
        currency_code="UAH",
    )


def _transfer_consumption():
    return ActiveFifoConsumptionCandidate(
        stock_lot_consumption_id=801,
        issue_document_id=901,
        issue_document_line_id=902,
        issue_event_date=date(2026, 1, 10),
        quantity=Decimal("5"),
    )


def test_without_router_preserves_old_transfer_issue_semantics():
    result = build_purchase_value_correction_fifo_impact_targets(
        stock_lot_id=100,
        receipt_quantity=Decimal("5"),
        current_consumed_quantity=Decimal("5"),
        active_allocation_peers=(
            _peer(),
        ),
        allocation_candidates=(
            _correction(),
        ),
        active_consumptions=(
            _transfer_consumption(),
        ),
    )

    assert len(result) == 1

    target = result[0]

    assert target.stock_lot_id == 100
    assert target.destination_kind == "issued"
    assert target.stock_lot_consumption_id == 801
    assert target.issue_document_id == 901
    assert target.issue_document_line_id == 902

    assert target.quantity == Decimal("5")
    assert target.original_base_amount == Decimal("50.00")
    assert target.corrected_base_amount == Decimal("60.00")


def test_transfer_router_replaces_source_transfer_issue_with_destination_on_hand():
    calls = []

    def router(
        *,
        source_consumption,
        local_start,
        local_end,
        source_recognition_date,
    ):
        calls.append(
            (
                source_consumption.stock_lot_consumption_id,
                local_start,
                local_end,
                source_recognition_date,
            )
        )

        return (
            FifoTransferRoutedDestinationSlice(
                stock_lot_id=1001,
                destination_kind="on_hand",
                stock_lot_consumption_id=None,
                issue_document_id=None,
                issue_document_line_id=None,
                quantity=Decimal("5"),
                recognition_date=source_recognition_date,
            ),
        )

    result = build_purchase_value_correction_fifo_impact_targets(
        stock_lot_id=100,
        receipt_quantity=Decimal("5"),
        current_consumed_quantity=Decimal("5"),
        active_allocation_peers=(
            _peer(),
        ),
        allocation_candidates=(
            _correction(),
        ),
        active_consumptions=(
            _transfer_consumption(),
        ),
        transfer_destination_router=router,
    )

    assert calls == [
        (
            801,
            Decimal("0"),
            Decimal("5"),
            date(2026, 2, 5),
        )
    ]

    assert len(result) == 1

    target = result[0]

    assert target.stock_lot_id == 1001
    assert target.destination_kind == "on_hand"
    assert target.stock_lot_consumption_id is None
    assert target.issue_document_id is None
    assert target.issue_document_line_id is None

    assert target.quantity == Decimal("5")
    assert target.original_base_amount == Decimal("50.00")
    assert target.corrected_base_amount == Decimal("60.00")


def test_transfer_router_can_split_destination_issued_and_on_hand_before_money():
    def router(
        *,
        source_consumption,
        local_start,
        local_end,
        source_recognition_date,
    ):
        assert source_consumption.stock_lot_consumption_id == 801
        assert local_start == Decimal("0")
        assert local_end == Decimal("5")

        return (
            FifoTransferRoutedDestinationSlice(
                stock_lot_id=1001,
                destination_kind="issued",
                stock_lot_consumption_id=1101,
                issue_document_id=1201,
                issue_document_line_id=1202,
                quantity=Decimal("3"),
                recognition_date=date(2026, 2, 10),
            ),
            FifoTransferRoutedDestinationSlice(
                stock_lot_id=1001,
                destination_kind="on_hand",
                stock_lot_consumption_id=None,
                issue_document_id=None,
                issue_document_line_id=None,
                quantity=Decimal("2"),
                recognition_date=source_recognition_date,
            ),
        )

    result = build_purchase_value_correction_fifo_impact_targets(
        stock_lot_id=100,
        receipt_quantity=Decimal("5"),
        current_consumed_quantity=Decimal("5"),
        active_allocation_peers=(
            _peer(),
        ),
        allocation_candidates=(
            _correction(),
        ),
        active_consumptions=(
            _transfer_consumption(),
        ),
        transfer_destination_router=router,
    )

    assert len(result) == 2

    issued, on_hand = result

    assert issued.stock_lot_id == 1001
    assert issued.destination_kind == "issued"
    assert issued.stock_lot_consumption_id == 1101
    assert issued.quantity == Decimal("3")
    assert issued.recognition_date == date(2026, 2, 10)

    assert on_hand.stock_lot_id == 1001
    assert on_hand.destination_kind == "on_hand"
    assert on_hand.quantity == Decimal("2")
    assert on_hand.recognition_date == date(2026, 2, 5)

    # 50 → 60 distributed after physical rerouting.
    assert issued.original_base_amount == Decimal("30.00")
    assert issued.corrected_base_amount == Decimal("36.00")

    assert on_hand.original_base_amount == Decimal("20.00")
    assert on_hand.corrected_base_amount == Decimal("24.00")

    assert sum(
        item.original_base_amount
        for item in result
    ) == Decimal("50.00")

    assert sum(
        item.corrected_base_amount
        for item in result
    ) == Decimal("60.00")


def test_non_transfer_consumption_falls_back_to_original_destination():
    def router(
        *,
        source_consumption,
        local_start,
        local_end,
        source_recognition_date,
    ):
        return None

    result = build_purchase_value_correction_fifo_impact_targets(
        stock_lot_id=100,
        receipt_quantity=Decimal("5"),
        current_consumed_quantity=Decimal("5"),
        active_allocation_peers=(
            _peer(),
        ),
        allocation_candidates=(
            _correction(),
        ),
        active_consumptions=(
            _transfer_consumption(),
        ),
        transfer_destination_router=router,
    )

    assert len(result) == 1
    assert result[0].stock_lot_id == 100
    assert result[0].stock_lot_consumption_id == 801


def test_partial_overlap_passes_local_transfer_offsets_not_global_offsets():
    peers = (
        ActiveFifoAllocationPeerCandidate(
            invoice_fulfillment_allocation_id=10,
            receipt_event_date=date(2026, 1, 1),
            quantity=Decimal("3"),
        ),
        ActiveFifoAllocationPeerCandidate(
            invoice_fulfillment_allocation_id=11,
            receipt_event_date=date(2026, 1, 2),
            quantity=Decimal("2"),
        ),
    )

    correction = PurchaseValueCorrectionFifoAllocationCandidate(
        purchase_value_correction_allocation_event_id=21,
        invoice_fulfillment_allocation_id=11,
        recognition_date=date(2026, 2, 5),
        quantity=Decimal("2"),
        original_allocated_base_amount=Decimal("20.00"),
        corrected_allocated_base_amount=Decimal("24.00"),
        currency_code="UAH",
    )

    # Consumption #700 occupies [0, 2).
    # Transfer #801 occupies [2, 5).
    # Corrected allocation occupies [3, 5).
    #
    # Therefore overlap with transfer is global [3, 5),
    # but local coordinates inside transfer layer must be [1, 3).
    consumptions = (
        ActiveFifoConsumptionCandidate(
            stock_lot_consumption_id=700,
            issue_document_id=701,
            issue_document_line_id=702,
            issue_event_date=date(2026, 1, 5),
            quantity=Decimal("2"),
        ),
        ActiveFifoConsumptionCandidate(
            stock_lot_consumption_id=801,
            issue_document_id=901,
            issue_document_line_id=902,
            issue_event_date=date(2026, 1, 10),
            quantity=Decimal("3"),
        ),
    )

    calls = []

    def router(
        *,
        source_consumption,
        local_start,
        local_end,
        source_recognition_date,
    ):
        if source_consumption.stock_lot_consumption_id != 801:
            return None

        calls.append(
            (
                local_start,
                local_end,
            )
        )

        return (
            FifoTransferRoutedDestinationSlice(
                stock_lot_id=1001,
                destination_kind="issued",
                stock_lot_consumption_id=1102,
                issue_document_id=1301,
                issue_document_line_id=1302,
                quantity=Decimal("2"),
                recognition_date=date(2026, 2, 12),
            ),
        )

    result = build_purchase_value_correction_fifo_impact_targets(
        stock_lot_id=100,
        receipt_quantity=Decimal("5"),
        current_consumed_quantity=Decimal("5"),
        active_allocation_peers=peers,
        allocation_candidates=(
            correction,
        ),
        active_consumptions=consumptions,
        transfer_destination_router=router,
    )

    assert calls == [
        (
            Decimal("1"),
            Decimal("3"),
        )
    ]

    assert len(result) == 1
    assert result[0].stock_lot_id == 1001
    assert result[0].stock_lot_consumption_id == 1102
    assert result[0].quantity == Decimal("2")
    assert result[0].original_base_amount == Decimal("20.00")
    assert result[0].corrected_base_amount == Decimal("24.00")


def test_router_result_must_conserve_source_overlap_quantity():
    def router(
        *,
        source_consumption,
        local_start,
        local_end,
        source_recognition_date,
    ):
        return (
            FifoTransferRoutedDestinationSlice(
                stock_lot_id=1001,
                destination_kind="on_hand",
                stock_lot_consumption_id=None,
                issue_document_id=None,
                issue_document_line_id=None,
                quantity=Decimal("4"),
                recognition_date=source_recognition_date,
            ),
        )

    import pytest

    with pytest.raises(
        Exception,
        match="transfer.*conserve|conserve.*transfer",
    ):
        build_purchase_value_correction_fifo_impact_targets(
            stock_lot_id=100,
            receipt_quantity=Decimal("5"),
            current_consumed_quantity=Decimal("5"),
            active_allocation_peers=(
                _peer(),
            ),
            allocation_candidates=(
                _correction(),
            ),
            active_consumptions=(
                _transfer_consumption(),
            ),
            transfer_destination_router=router,
        )
