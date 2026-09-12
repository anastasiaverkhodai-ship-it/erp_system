from datetime import date
from decimal import Decimal

import pytest

import app.services.inventory_count_reconciliation_service as service
from app.models.inventory_count_event import (
    InventoryCountEvent,
)
from app.services.inventory_count_history_adapter_service import (
    InventoryCountHistoryAdapterError,
    inventory_count_rows_to_history,
)
from app.services.inventory_count_history_service import (
    InventoryCountHistoryAction,
    normalize_inventory_count_target,
)


KEY = "33333333-3333-4333-8333-333333333333"
OTHER_KEY = "44444444-4444-4444-8444-444444444444"

D1 = date(2026, 9, 10)
D2 = date(2026, 9, 11)


def event(
    *,
    row_id=1,
    reversal_of_id=None,
    history_key=KEY,
    expected="10",
    counted="8",
):
    return InventoryCountEvent(
        id=row_id,
        company_id=1,
        history_key=history_key,
        product_id=100,
        warehouse_id=10,
        count_date=D1,
        expected_quantity=Decimal(expected),
        counted_quantity=Decimal(counted),
        created_by=5,
        reversal_of_id=reversal_of_id,
    )


def target(
    *,
    expected="10",
    counted="8",
    count_date=D1,
):
    return normalize_inventory_count_target(
        company_id=1,
        product_id=100,
        warehouse_id=10,
        count_date=count_date,
        expected_quantity=Decimal(expected),
        counted_quantity=Decimal(counted),
    )


def test_count_adapter_reconstructs_original():
    history = inventory_count_rows_to_history(
        events=(event(),),
        history_key=KEY,
    )

    assert history[0].target == target()
    assert history[0].reversal_of_id is None


def test_count_adapter_reversal_has_no_target():
    history = inventory_count_rows_to_history(
        events=(
            event(row_id=1),
            event(
                row_id=2,
                reversal_of_id=1,
            ),
        ),
        history_key=KEY,
    )

    assert history[1].target is None
    assert history[1].reversal_of_id == 1


def test_count_adapter_rejects_cross_chain_row():
    with pytest.raises(
        InventoryCountHistoryAdapterError,
        match="different history_key",
    ):
        inventory_count_rows_to_history(
            events=(
                event(
                    history_key=OTHER_KEY,
                ),
            ),
            history_key=KEY,
        )


@pytest.mark.asyncio
async def test_prepare_count_reconciliation_create(
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
            (company_id, history_key)
        )
        return ()

    monkeypatch.setattr(
        service,
        "lock_inventory_count_history",
        fake_lock,
    )

    plan = await service.prepare_inventory_count_reconciliation(
        object(),
        company_id=1,
        history_key=KEY,
        target=target(),
    )

    assert plan.action == InventoryCountHistoryAction.CREATE
    assert calls == [(1, KEY)]


@pytest.mark.asyncio
async def test_prepare_count_reconciliation_noop(
    monkeypatch,
):
    async def fake_lock(*args, **kwargs):
        return (event(),)

    monkeypatch.setattr(
        service,
        "lock_inventory_count_history",
        fake_lock,
    )

    plan = await service.prepare_inventory_count_reconciliation(
        object(),
        company_id=1,
        history_key=KEY,
        target=target(),
    )

    assert plan.action == InventoryCountHistoryAction.NOOP


@pytest.mark.asyncio
async def test_prepare_count_reconciliation_reversal(
    monkeypatch,
):
    async def fake_lock(*args, **kwargs):
        return (event(),)

    monkeypatch.setattr(
        service,
        "lock_inventory_count_history",
        fake_lock,
    )

    plan = await service.prepare_inventory_count_reconciliation(
        object(),
        company_id=1,
        history_key=KEY,
        target=None,
        adjustment_date=D2,
    )

    assert plan.action == InventoryCountHistoryAction.REVERSE
    assert plan.active_original.id == 1


@pytest.mark.asyncio
async def test_prepare_count_reconciliation_replacement(
    monkeypatch,
):
    async def fake_lock(*args, **kwargs):
        return (event(),)

    monkeypatch.setattr(
        service,
        "lock_inventory_count_history",
        fake_lock,
    )

    plan = await service.prepare_inventory_count_reconciliation(
        object(),
        company_id=1,
        history_key=KEY,
        target=target(
            counted="9",
            count_date=D2,
        ),
        adjustment_date=D2,
    )

    assert (
        plan.action
        == InventoryCountHistoryAction.REVERSE_AND_REPLACE
    )
