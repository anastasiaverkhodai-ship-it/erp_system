from datetime import date
from decimal import Decimal

import pytest

import app.services.warehouse_transfer_reconciliation_service as service
from app.models.warehouse_transfer_event import (
    WarehouseTransferEvent,
)
from app.models.warehouse_transfer_line import (
    WarehouseTransferLine,
)
from app.services.warehouse_transfer_history_adapter_service import (
    WarehouseTransferHistoryAdapterError,
    warehouse_transfer_rows_to_history,
)
from app.services.warehouse_transfer_history_service import (
    WarehouseTransferHistoryAction,
    WarehouseTransferLineTarget,
    normalize_transfer_target,
)


KEY = "11111111-1111-4111-8111-111111111111"
D1 = date(2026, 9, 10)
D2 = date(2026, 9, 11)


def event(
    *,
    row_id=1,
    reversal_of_id=None,
    history_key=KEY,
):
    return WarehouseTransferEvent(
        id=row_id,
        company_id=1,
        history_key=history_key,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        transfer_date=D1,
        created_by=5,
        reversal_of_id=reversal_of_id,
    )


def line(
    *,
    event_id=1,
    product_id=100,
    quantity="5",
):
    return WarehouseTransferLine(
        id=10 + product_id,
        company_id=1,
        transfer_event_id=event_id,
        product_id=product_id,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        quantity=Decimal(quantity),
        issue_document_id=30,
        issue_document_line_id=31 + product_id,
        receipt_document_id=40,
    )


def target(
    *,
    quantity="5",
    transfer_date=D1,
):
    return normalize_transfer_target(
        company_id=1,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        transfer_date=transfer_date,
        lines=(
            WarehouseTransferLineTarget(
                product_id=100,
                quantity=Decimal(quantity),
            ),
        ),
    )


def test_adapter_reconstructs_original_target():
    history = warehouse_transfer_rows_to_history(
        events=(event(),),
        lines=(line(),),
        history_key=KEY,
    )

    assert len(history) == 1
    assert history[0].target == target()
    assert history[0].reversal_of_id is None


def test_adapter_reversal_has_no_target():
    history = warehouse_transfer_rows_to_history(
        events=(
            event(row_id=1),
            event(
                row_id=2,
                reversal_of_id=1,
            ),
        ),
        lines=(line(event_id=1),),
        history_key=KEY,
    )

    assert history[1].reversal_of_id == 1
    assert history[1].target is None


def test_adapter_rejects_original_without_physical_lines():
    with pytest.raises(
        WarehouseTransferHistoryAdapterError,
        match="requires at least one",
    ):
        warehouse_transfer_rows_to_history(
            events=(event(),),
            lines=(),
            history_key=KEY,
        )


def test_adapter_rejects_orphan_line():
    with pytest.raises(
        WarehouseTransferHistoryAdapterError,
        match="orphan",
    ):
        warehouse_transfer_rows_to_history(
            events=(event(),),
            lines=(line(event_id=999),),
            history_key=KEY,
        )


def test_adapter_rejects_duplicate_product_lines():
    with pytest.raises(
        WarehouseTransferHistoryAdapterError,
        match="duplicate product",
    ):
        warehouse_transfer_rows_to_history(
            events=(event(),),
            lines=(
                line(product_id=100),
                line(product_id=100),
            ),
            history_key=KEY,
        )


@pytest.mark.asyncio
async def test_prepare_transfer_reconciliation_create(
    monkeypatch,
):
    calls = []

    async def fake_lock(
        db,
        *,
        company_id,
        history_key,
    ):
        calls.append(
            ("lock", company_id, history_key)
        )
        return ()

    async def fake_lines(
        db,
        *,
        transfer_event_ids,
    ):
        calls.append(
            ("lines", transfer_event_ids)
        )
        return ()

    monkeypatch.setattr(
        service,
        "lock_warehouse_transfer_history",
        fake_lock,
    )
    monkeypatch.setattr(
        service,
        "load_warehouse_transfer_lines",
        fake_lines,
    )

    plan = await service.prepare_warehouse_transfer_reconciliation(
        object(),
        company_id=1,
        history_key=KEY,
        target=target(),
    )

    assert plan.action == WarehouseTransferHistoryAction.CREATE
    assert calls == [
        ("lock", 1, KEY),
        ("lines", ()),
    ]


@pytest.mark.asyncio
async def test_prepare_transfer_reconciliation_noop(
    monkeypatch,
):
    async def fake_lock(*args, **kwargs):
        return (event(),)

    async def fake_lines(*args, **kwargs):
        return (line(),)

    monkeypatch.setattr(
        service,
        "lock_warehouse_transfer_history",
        fake_lock,
    )
    monkeypatch.setattr(
        service,
        "load_warehouse_transfer_lines",
        fake_lines,
    )

    plan = await service.prepare_warehouse_transfer_reconciliation(
        object(),
        company_id=1,
        history_key=KEY,
        target=target(),
    )

    assert plan.action == WarehouseTransferHistoryAction.NOOP


@pytest.mark.asyncio
async def test_prepare_transfer_reconciliation_replacement(
    monkeypatch,
):
    async def fake_lock(*args, **kwargs):
        return (event(),)

    async def fake_lines(*args, **kwargs):
        return (line(),)

    monkeypatch.setattr(
        service,
        "lock_warehouse_transfer_history",
        fake_lock,
    )
    monkeypatch.setattr(
        service,
        "load_warehouse_transfer_lines",
        fake_lines,
    )

    plan = await service.prepare_warehouse_transfer_reconciliation(
        object(),
        company_id=1,
        history_key=KEY,
        target=target(
            quantity="7",
            transfer_date=D2,
        ),
        adjustment_date=D2,
    )

    assert (
        plan.action
        == WarehouseTransferHistoryAction.REVERSE_AND_REPLACE
    )
    assert plan.active_original.id == 1
