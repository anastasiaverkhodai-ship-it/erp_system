from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import ast

import pytest

import app.services.purchase_value_correction_fifo_impact_reconciliation_service as service

from app.models.purchase_value_correction_allocation_event import (
    PurchaseValueCorrectionAllocationEvent,
)
from app.models.purchase_value_correction_fifo_impact_event import (
    PurchaseValueCorrectionFifoImpactEvent,
)
from app.services.purchase_value_correction_fifo_impact_calculation_service import (
    ActiveFifoConsumptionCandidate,
)
from app.services.purchase_value_correction_fifo_impact_reconciliation_service import (
    PurchaseValueCorrectionFifoImpactReconciliationChronologyError,
    reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line,
)


D1 = date(2026, 9, 1)
D2 = date(2026, 9, 2)
D3 = date(2026, 9, 3)
D4 = date(2026, 9, 4)
D5 = date(2026, 9, 5)


def ifa(
    event_id,
    quantity,
):
    return SimpleNamespace(
        id=event_id,
        quantity=Decimal(
            quantity
        ),
    )


def pvca(
    *,
    event_id=700,
    ifa_id=12,
    recognition_date=D3,
    original="50.00",
    corrected="45.00",
):
    value = (
        PurchaseValueCorrectionAllocationEvent(
            company_id=1,
            trade_value_correction_event_id=900,
            invoice_fulfillment_allocation_id=ifa_id,
            recognition_date=recognition_date,
            original_allocated_base_amount=Decimal(
                original
            ),
            corrected_allocated_base_amount=Decimal(
                corrected
            ),
            currency_code="UAH",
            created_by=1,
            reversal_of_id=None,
        )
    )

    value.id = event_id

    return value


def impact(
    *,
    event_id,
    allocation_event_id=700,
    destination_kind="issued",
    consumption_id=801,
    issue_document_id=901,
    issue_line_id=902,
    quantity="10",
    recognition_date=D3,
    original="10.00",
    corrected="9.00",
    reversal_of_id=None,
):
    value = (
        PurchaseValueCorrectionFifoImpactEvent(
            company_id=1,
            purchase_value_correction_allocation_event_id=(
                allocation_event_id
            ),
            stock_lot_id=600,
            destination_kind=destination_kind,
            stock_lot_consumption_id=(
                consumption_id
                if destination_kind == "issued"
                else None
            ),
            issue_document_id=(
                issue_document_id
                if destination_kind == "issued"
                else None
            ),
            issue_document_line_id=(
                issue_line_id
                if destination_kind == "issued"
                else None
            ),
            recognition_date=recognition_date,
            quantity=Decimal(
                quantity
            ),
            original_base_amount=Decimal(
                original
            ),
            corrected_base_amount=Decimal(
                corrected
            ),
            currency_code="UAH",
            created_by=1,
            reversal_of_id=reversal_of_id,
        )
    )

    value.id = event_id

    return value


def install_loaders(
    monkeypatch,
    *,
    active_ifas,
    pvca_history,
    consumptions,
    impact_history,
    remaining="60",
):
    fulfillment_line = SimpleNamespace(
        id=100,
        warehouse_document_id=500,
        warehouse_document_line_id=501,
        product_id=10,
        warehouse_id=20,
        quantity=Decimal("100"),
    )

    receipt = SimpleNamespace(
        document_date=D1,
    )

    lot = SimpleNamespace(
        id=600,
        source_document_id=500,
        source_document_line_id=501,
        product_id=10,
        warehouse_id=20,
        original_quantity=Decimal("100"),
        remaining_quantity=Decimal(
            remaining
        ),
    )

    async def load_line(
        db,
        *,
        company_id,
        fulfillment_line_id,
    ):
        return (
            fulfillment_line,
            receipt,
        )

    async def load_lot(
        db,
        *,
        company_id,
        fulfillment_line,
    ):
        return lot

    async def load_ifas(
        db,
        *,
        company_id,
        fulfillment_line_id,
    ):
        return tuple(
            active_ifas
        )

    async def load_pvca(
        db,
        *,
        company_id,
        fulfillment_line_id,
    ):
        return tuple(
            pvca_history
        )

    async def load_consumptions(
        db,
        *,
        company_id,
        stock_lot_id,
    ):
        return tuple(
            consumptions
        )

    async def load_impacts(
        db,
        *,
        company_id,
        fulfillment_line_id,
    ):
        return tuple(
            impact_history
        )

    monkeypatch.setattr(
        service,
        "_lock_fulfillment_line_and_receipt",
        load_line,
    )

    monkeypatch.setattr(
        service,
        "_lock_stock_lot",
        load_lot,
    )

    monkeypatch.setattr(
        service,
        "_load_active_ifa_peers_for_update",
        load_ifas,
    )

    monkeypatch.setattr(
        service,
        "_load_pvca_history_for_update",
        load_pvca,
    )

    monkeypatch.setattr(
        service,
        "_load_active_consumption_candidates_for_update",
        load_consumptions,
    )

    monkeypatch.setattr(
        service,
        "_load_impact_history_for_update",
        load_impacts,
    )


def install_persistence(
    monkeypatch,
):
    calls = []

    async def reconcile(
        db,
        *,
        company_id,
        target,
        created_by,
        reversal_date=None,
    ):
        calls.append(
            (
                target,
                reversal_date,
            )
        )

        return ()

    monkeypatch.setattr(
        service,
        "reconcile_purchase_value_correction_fifo_impact_source",
        reconcile,
    )

    return calls


@pytest.mark.asyncio
async def test_all_active_peers_define_interval_for_one_correction(
    monkeypatch,
):
    source = pvca()

    install_loaders(
        monkeypatch,
        active_ifas=(
            ifa(11, "30"),
            ifa(12, "50"),
            ifa(13, "20"),
        ),
        pvca_history=(
            source,
        ),
        consumptions=(
            ActiveFifoConsumptionCandidate(
                stock_lot_consumption_id=801,
                issue_document_id=901,
                issue_document_line_id=902,
                issue_event_date=D2,
                quantity=Decimal("40"),
            ),
        ),
        impact_history=(),
        remaining="60",
    )

    calls = install_persistence(
        monkeypatch
    )

    result = (
        await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_line_id=100,
            adjustment_date=D3,
            created_by=2,
        )
    )

    assert (
        result.active_allocation_peer_ids
        == (
            11,
            12,
            13,
        )
    )

    assert (
        result.active_correction_allocation_event_ids
        == (
            700,
        )
    )

    assert tuple(
        (
            target.destination_kind,
            target.quantity,
        )
        for target in result.desired_targets
    ) == (
        (
            "issued",
            Decimal("10"),
        ),
        (
            "on_hand",
            Decimal("40"),
        ),
    )

    assert len(
        calls
    ) == 2


@pytest.mark.asyncio
async def test_unchanged_state_preserves_persisted_date(
    monkeypatch,
):
    source = pvca(
        ifa_id=12,
        recognition_date=D3,
    )

    current = impact(
        event_id=1001,
        destination_kind="on_hand",
        quantity="50",
        recognition_date=D4,
        original="50.00",
        corrected="45.00",
    )

    install_loaders(
        monkeypatch,
        active_ifas=(
            ifa(12, "50"),
        ),
        pvca_history=(
            source,
        ),
        consumptions=(),
        impact_history=(
            current,
        ),
        remaining="100",
    )

    calls = install_persistence(
        monkeypatch
    )

    result = (
        await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_line_id=100,
            adjustment_date=D5,
            created_by=2,
        )
    )

    target = (
        result.reconciliation_targets[
            0
        ]
    )

    assert (
        target.recognition_date
        == D4
    )

    assert calls[0][1] == D5


@pytest.mark.asyncio
async def test_removed_destination_is_reversed_first(
    monkeypatch,
):
    source = pvca()

    current = impact(
        event_id=1001,
        destination_kind="issued",
        quantity="10",
        recognition_date=D3,
        original="10.00",
        corrected="9.00",
    )

    install_loaders(
        monkeypatch,
        active_ifas=(),
        pvca_history=(
            source,
        ),
        consumptions=(),
        impact_history=(
            current,
        ),
        remaining="100",
    )

    calls = install_persistence(
        monkeypatch
    )

    await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
        object(),
        company_id=1,
        fulfillment_line_id=100,
        adjustment_date=D5,
        created_by=2,
    )

    assert len(
        calls
    ) == 1

    target, reversal_date = calls[0]

    assert target.is_noop
    assert (
        target.destination_kind
        == "issued"
    )
    assert reversal_date == D5


@pytest.mark.asyncio
async def test_new_topology_destination_uses_adjustment_date(
    monkeypatch,
):
    source = pvca(
        recognition_date=D1,
        original="100.00",
        corrected="90.00",
    )

    current = impact(
        event_id=1001,
        destination_kind="issued",
        quantity="100",
        recognition_date=D2,
        original="100.00",
        corrected="90.00",
    )

    install_loaders(
        monkeypatch,
        active_ifas=(
            ifa(12, "100"),
        ),
        pvca_history=(
            source,
        ),
        consumptions=(),
        impact_history=(
            current,
        ),
        remaining="100",
    )

    calls = install_persistence(
        monkeypatch
    )

    result = (
        await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_line_id=100,
            adjustment_date=D5,
            created_by=2,
        )
    )

    assert len(
        calls
    ) == 2

    assert calls[0][0].is_noop
    assert (
        calls[1][0].destination_kind
        == "on_hand"
    )
    assert (
        calls[1][0].recognition_date
        == D5
    )

    assert tuple(
        target.recognition_date
        for target
        in result.desired_targets
    ) == (
        D1,
    )


@pytest.mark.asyncio
async def test_brand_new_source_keeps_primary_date(
    monkeypatch,
):
    source = pvca(
        recognition_date=D1,
        original="100.00",
        corrected="90.00",
    )

    install_loaders(
        monkeypatch,
        active_ifas=(
            ifa(12, "100"),
        ),
        pvca_history=(
            source,
        ),
        consumptions=(),
        impact_history=(),
        remaining="100",
    )

    calls = install_persistence(
        monkeypatch
    )

    await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
        object(),
        company_id=1,
        fulfillment_line_id=100,
        adjustment_date=D5,
        created_by=2,
    )

    assert len(
        calls
    ) == 1

    assert (
        calls[0][0].recognition_date
        == D1
    )

    assert calls[0][1] is None


@pytest.mark.asyncio
async def test_multiple_active_corrections_same_ifa_are_calculated_separately(
    monkeypatch,
):
    first = pvca(
        event_id=700,
        ifa_id=12,
        original="100.00",
        corrected="90.00",
    )

    second = (
        PurchaseValueCorrectionAllocationEvent(
            company_id=1,
            trade_value_correction_event_id=901,
            invoice_fulfillment_allocation_id=12,
            recognition_date=D4,
            original_allocated_base_amount=Decimal("90.00"),
            corrected_allocated_base_amount=Decimal("80.00"),
            currency_code="UAH",
            created_by=1,
            reversal_of_id=None,
        )
    )

    second.id = 701

    install_loaders(
        monkeypatch,
        active_ifas=(
            ifa(12, "100"),
        ),
        pvca_history=(
            first,
            second,
        ),
        consumptions=(),
        impact_history=(),
        remaining="100",
    )

    calls = install_persistence(
        monkeypatch
    )

    result = (
        await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_line_id=100,
            adjustment_date=D5,
            created_by=2,
        )
    )

    assert (
        result.active_correction_allocation_event_ids
        == (
            700,
            701,
        )
    )

    assert len(
        result.desired_targets
    ) == 2

    assert len(
        calls
    ) == 2


@pytest.mark.asyncio
async def test_adjustment_date_cannot_predate_removed_current(
    monkeypatch,
):
    source = pvca()

    current = impact(
        event_id=1001,
        recognition_date=D4,
    )

    install_loaders(
        monkeypatch,
        active_ifas=(),
        pvca_history=(
            source,
        ),
        consumptions=(),
        impact_history=(
            current,
        ),
        remaining="100",
    )

    calls = install_persistence(
        monkeypatch
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoImpactReconciliationChronologyError,
        match="removed active",
    ):
        await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_line_id=100,
            adjustment_date=D3,
            created_by=2,
        )

    assert calls == []


def test_service_has_no_commit_or_rollback():
    path = Path(
        "app/services/"
        "purchase_value_correction_fifo_"
        "impact_reconciliation_service.py"
    )

    tree = ast.parse(
        path.read_text()
    )

    for node in ast.walk(tree):
        if not isinstance(
            node,
            ast.Call,
        ):
            continue

        if not isinstance(
            node.func,
            ast.Attribute,
        ):
            continue

        assert node.func.attr not in {
            "commit",
            "rollback",
        }
