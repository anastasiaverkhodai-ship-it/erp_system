from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.inventory_count_reconciliation_executor as executor
from app.services.inventory_count_history_service import (
    InventoryCountHistoryAction,
    InventoryCountHistoryPlan,
    normalize_inventory_count_target,
)
from app.services.inventory_count_physical_factory import (
    InventoryCountPhysicalDirection,
    InventoryCountPhysicalResult,
)


KEY = "33333333-3333-4333-8333-333333333333"
D1 = date(2026, 9, 10)
D2 = date(2026, 9, 11)


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


class FakeDB:
    def __init__(self):
        self.added = []
        self.flush_count = 0

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flush_count += 1


class FakeFactory:
    def __init__(self):
        self.calls = []

    async def create_variance_adjustment(
        self,
        db,
        *,
        target,
        created_by,
    ):
        self.calls.append(
            ("create", target, created_by)
        )

        variance = target.variance_quantity

        if variance == 0:
            return None

        return InventoryCountPhysicalResult(
            document_id=30,
            document_line_id=31,
            product_id=target.product_id,
            warehouse_id=target.warehouse_id,
            direction=(
                InventoryCountPhysicalDirection.INCREASE
                if variance > 0
                else InventoryCountPhysicalDirection.DECREASE
            ),
            quantity=abs(variance),
        )

    async def reverse_variance_adjustment(
        self,
        db,
        *,
        original_event,
        reversal_date,
        created_by,
    ):
        self.calls.append(
            (
                "reverse",
                original_event.id,
                reversal_date,
                created_by,
            )
        )


@pytest.mark.asyncio
async def test_zero_variance_creates_snapshot_but_no_adjustment(
    monkeypatch,
):
    value = target(
        expected="10",
        counted="10",
    )

    plan = InventoryCountHistoryPlan(
        history_key=KEY,
        action=InventoryCountHistoryAction.CREATE,
        active_original=None,
        target=value,
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    async def append_event(*args, **kwargs):
        return SimpleNamespace(id=50)

    monkeypatch.setattr(
        executor,
        "prepare_inventory_count_reconciliation",
        prepare,
    )
    monkeypatch.setattr(
        executor,
        "append_inventory_count_event",
        append_event,
    )

    factory = FakeFactory()
    db = FakeDB()

    await executor.execute_inventory_count_reconciliation(
        db,
        factory=factory,
        company_id=1,
        history_key=KEY,
        target=value,
        adjustment_date=None,
        created_by=7,
    )

    assert len(factory.calls) == 1
    assert factory.calls[0][0] == "create"
    assert db.added == []


@pytest.mark.asyncio
async def test_negative_variance_creates_decrease_event(
    monkeypatch,
):
    value = target(
        expected="10",
        counted="8",
    )

    plan = InventoryCountHistoryPlan(
        history_key=KEY,
        action=InventoryCountHistoryAction.CREATE,
        active_original=None,
        target=value,
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    async def append_event(*args, **kwargs):
        return SimpleNamespace(id=50)

    monkeypatch.setattr(
        executor,
        "prepare_inventory_count_reconciliation",
        prepare,
    )
    monkeypatch.setattr(
        executor,
        "append_inventory_count_event",
        append_event,
    )

    db = FakeDB()

    await executor.execute_inventory_count_reconciliation(
        db,
        factory=FakeFactory(),
        company_id=1,
        history_key=KEY,
        target=value,
        adjustment_date=None,
        created_by=7,
    )

    assert len(db.added) == 1

    variance = db.added[0]

    assert variance.inventory_count_event_id == 50
    assert variance.quantity == Decimal("2")
    assert variance.direction.value == "decrease"
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_positive_variance_creates_increase_event(
    monkeypatch,
):
    value = target(
        expected="10",
        counted="12",
    )

    plan = InventoryCountHistoryPlan(
        history_key=KEY,
        action=InventoryCountHistoryAction.CREATE,
        active_original=None,
        target=value,
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    async def append_event(*args, **kwargs):
        return SimpleNamespace(id=50)

    monkeypatch.setattr(
        executor,
        "prepare_inventory_count_reconciliation",
        prepare,
    )
    monkeypatch.setattr(
        executor,
        "append_inventory_count_event",
        append_event,
    )

    db = FakeDB()

    await executor.execute_inventory_count_reconciliation(
        db,
        factory=FakeFactory(),
        company_id=1,
        history_key=KEY,
        target=value,
        adjustment_date=None,
        created_by=7,
    )

    assert db.added[0].direction.value == "increase"
    assert db.added[0].quantity == Decimal("2")


@pytest.mark.asyncio
async def test_nonzero_variance_requires_physical_result(
    monkeypatch,
):
    value = target()

    plan = InventoryCountHistoryPlan(
        history_key=KEY,
        action=InventoryCountHistoryAction.CREATE,
        active_original=None,
        target=value,
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    async def append_event(*args, **kwargs):
        return SimpleNamespace(id=50)

    class Factory(FakeFactory):
        async def create_variance_adjustment(
            self,
            *args,
            **kwargs,
        ):
            return None

    monkeypatch.setattr(
        executor,
        "prepare_inventory_count_reconciliation",
        prepare,
    )
    monkeypatch.setattr(
        executor,
        "append_inventory_count_event",
        append_event,
    )

    with pytest.raises(
        executor.InventoryCountExecutionError,
        match="requires physical adjustment",
    ):
        await executor.execute_inventory_count_reconciliation(
            FakeDB(),
            factory=Factory(),
            company_id=1,
            history_key=KEY,
            target=value,
            adjustment_date=None,
            created_by=7,
        )


@pytest.mark.asyncio
async def test_count_replacement_orders_reverse_then_new_snapshot(
    monkeypatch,
):
    calls = []

    original = SimpleNamespace(
        id=10,
        company_id=1,
        product_id=100,
        warehouse_id=10,
        expected_quantity=Decimal("10"),
        counted_quantity=Decimal("8"),
    )

    replacement = target(
        expected="10",
        counted="9",
        count_date=D2,
    )

    plan = InventoryCountHistoryPlan(
        history_key=KEY,
        action=(
            InventoryCountHistoryAction.REVERSE_AND_REPLACE
        ),
        active_original=original,
        target=replacement,
        reversal_date=D2,
    )

    async def prepare(*args, **kwargs):
        return plan

    class Factory(FakeFactory):
        async def reverse_variance_adjustment(
            self,
            *args,
            **kwargs,
        ):
            calls.append("reverse")

        async def create_variance_adjustment(
            self,
            *args,
            **kwargs,
        ):
            calls.append("create")
            return InventoryCountPhysicalResult(
                document_id=30,
                document_line_id=31,
                product_id=100,
                warehouse_id=10,
                direction=(
                    InventoryCountPhysicalDirection.DECREASE
                ),
                quantity=Decimal("1"),
            )

    ids = iter((50, 60))

    async def append_event(*args, **kwargs):
        row_id = next(ids)
        calls.append(
            f"event_{row_id}"
        )
        return SimpleNamespace(id=row_id)

    monkeypatch.setattr(
        executor,
        "prepare_inventory_count_reconciliation",
        prepare,
    )
    monkeypatch.setattr(
        executor,
        "append_inventory_count_event",
        append_event,
    )

    await executor.execute_inventory_count_reconciliation(
        FakeDB(),
        factory=Factory(),
        company_id=1,
        history_key=KEY,
        target=replacement,
        adjustment_date=D2,
        created_by=7,
    )

    assert calls == [
        "reverse",
        "event_50",
        "event_60",
        "create",
    ]
