from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.purchase_value_correction_fifo_impact_reconciliation_service as reconciliation

from app.services.purchase_value_correction_fifo_impact_calculation_service import (
    ActiveFifoAllocationPeerCandidate,
    ActiveFifoConsumptionCandidate,
    PurchaseValueCorrectionFifoAllocationCandidate,
    PurchaseValueCorrectionFifoImpactTarget,
)


class _Router:
    def __init__(self):
        self.calls = []

    def route(
        self,
        *,
        source_consumption,
        local_start,
        local_end,
        source_recognition_date,
    ):
        self.calls.append(
            (
                source_consumption.stock_lot_consumption_id,
                local_start,
                local_end,
                source_recognition_date,
            )
        )

        return None


@pytest.mark.asyncio
async def test_preload_helper_delegates_exact_active_consumptions(
    monkeypatch,
):
    calls = []
    router = _Router()

    async def fake_preload(
        db,
        *,
        company_id,
        active_consumptions,
    ):
        calls.append(
            (
                db,
                company_id,
                active_consumptions,
            )
        )

        return router

    monkeypatch.setattr(
        reconciliation,
        "preload_purchase_value_correction_fifo_transfer_router",
        fake_preload,
    )

    db = object()

    consumptions = (
        ActiveFifoConsumptionCandidate(
            stock_lot_consumption_id=801,
            issue_document_id=901,
            issue_document_line_id=902,
            issue_event_date=date(
                2026,
                1,
                10,
            ),
            quantity=Decimal("5"),
        ),
    )

    result = (
        await reconciliation._preload_fifo_transfer_router(
            db,
            company_id=1,
            active_consumptions=consumptions,
        )
    )

    assert result is router

    assert calls == [
        (
            db,
            1,
            consumptions,
        )
    ]


def test_calculation_helper_passes_router_callback_to_fifo_calculator(
    monkeypatch,
):
    router = _Router()
    captured = {}

    expected = (
        PurchaseValueCorrectionFifoImpactTarget(
            purchase_value_correction_allocation_event_id=21,
            invoice_fulfillment_allocation_id=11,
            stock_lot_id=1001,
            destination_kind="on_hand",
            stock_lot_consumption_id=None,
            issue_document_id=None,
            issue_document_line_id=None,
            quantity=Decimal("5"),
            recognition_date=date(
                2026,
                2,
                5,
            ),
            original_base_amount=Decimal(
                "50.00"
            ),
            corrected_base_amount=Decimal(
                "60.00"
            ),
            currency_code="UAH",
        ),
    )

    def fake_calculator(**kwargs):
        captured.update(
            kwargs
        )

        return expected

    monkeypatch.setattr(
        reconciliation,
        "build_purchase_value_correction_fifo_impact_targets",
        fake_calculator,
    )

    peers = (
        ActiveFifoAllocationPeerCandidate(
            invoice_fulfillment_allocation_id=11,
            receipt_event_date=date(
                2026,
                1,
                1,
            ),
            quantity=Decimal("5"),
        ),
    )

    sources = (
        PurchaseValueCorrectionFifoAllocationCandidate(
            purchase_value_correction_allocation_event_id=21,
            invoice_fulfillment_allocation_id=11,
            recognition_date=date(
                2026,
                2,
                5,
            ),
            quantity=Decimal("5"),
            original_allocated_base_amount=Decimal(
                "50.00"
            ),
            corrected_allocated_base_amount=Decimal(
                "60.00"
            ),
            currency_code="UAH",
        ),
    )

    consumptions = (
        ActiveFifoConsumptionCandidate(
            stock_lot_consumption_id=801,
            issue_document_id=901,
            issue_document_line_id=902,
            issue_event_date=date(
                2026,
                1,
                10,
            ),
            quantity=Decimal("5"),
        ),
    )

    result = (
        reconciliation._calculate_fifo_targets_with_transfer_router(
            stock_lot_id=100,
            receipt_quantity=Decimal("5"),
            current_consumed_quantity=Decimal("5"),
            active_allocation_peers=peers,
            allocation_candidates=sources,
            active_consumptions=consumptions,
            transfer_router=router,
        )
    )

    assert result == expected

    assert captured[
        "stock_lot_id"
    ] == 100

    assert captured[
        "active_consumptions"
    ] == consumptions

    assert (
        captured[
            "transfer_destination_router"
        ]
        == router.route
    )


def test_calculation_helper_supports_no_transfer_router(
    monkeypatch,
):
    captured = {}

    def fake_calculator(**kwargs):
        captured.update(
            kwargs
        )

        return ()

    monkeypatch.setattr(
        reconciliation,
        "build_purchase_value_correction_fifo_impact_targets",
        fake_calculator,
    )

    result = (
        reconciliation._calculate_fifo_targets_with_transfer_router(
            stock_lot_id=100,
            receipt_quantity=Decimal("5"),
            current_consumed_quantity=Decimal("0"),
            active_allocation_peers=(),
            allocation_candidates=(),
            active_consumptions=(),
            transfer_router=None,
        )
    )

    assert result == ()

    assert (
        captured[
            "transfer_destination_router"
        ]
        is None
    )


def test_target_helper_preserves_router_generated_destination():
    router = SimpleNamespace(
        route=lambda **kwargs: (
            reconciliation.FifoTransferRoutedDestinationSlice(
                stock_lot_id=1001,
                destination_kind="on_hand",
                stock_lot_consumption_id=None,
                issue_document_id=None,
                issue_document_line_id=None,
                quantity=Decimal("5"),
                recognition_date=date(
                    2026,
                    2,
                    5,
                ),
            ),
        )
    )

    result = (
        reconciliation._calculate_fifo_targets_with_transfer_router(
            stock_lot_id=100,
            receipt_quantity=Decimal("5"),
            current_consumed_quantity=Decimal("5"),
            active_allocation_peers=(
                ActiveFifoAllocationPeerCandidate(
                    invoice_fulfillment_allocation_id=11,
                    receipt_event_date=date(
                        2026,
                        1,
                        1,
                    ),
                    quantity=Decimal("5"),
                ),
            ),
            allocation_candidates=(
                PurchaseValueCorrectionFifoAllocationCandidate(
                    purchase_value_correction_allocation_event_id=21,
                    invoice_fulfillment_allocation_id=11,
                    recognition_date=date(
                        2026,
                        2,
                        5,
                    ),
                    quantity=Decimal("5"),
                    original_allocated_base_amount=Decimal(
                        "50.00"
                    ),
                    corrected_allocated_base_amount=Decimal(
                        "60.00"
                    ),
                    currency_code="UAH",
                ),
            ),
            active_consumptions=(
                ActiveFifoConsumptionCandidate(
                    stock_lot_consumption_id=801,
                    issue_document_id=901,
                    issue_document_line_id=902,
                    issue_event_date=date(
                        2026,
                        1,
                        10,
                    ),
                    quantity=Decimal("5"),
                ),
            ),
            transfer_router=router,
        )
    )

    assert len(result) == 1

    assert result[0].stock_lot_id == 1001
    assert result[0].destination_kind == "on_hand"
    assert (
        result[0].stock_lot_consumption_id
        is None
    )
