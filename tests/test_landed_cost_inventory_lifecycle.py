from datetime import date
from unittest.mock import AsyncMock

import pytest
import app.services.landed_cost_inventory_lifecycle as lifecycle


@pytest.mark.asyncio
async def test_nested_operation_reconciles_once_after_all_steps(monkeypatch):
    calls = []
    async def lock(db, company_id): calls.append("lock")
    async def reconcile(*args, **kwargs): calls.append("reconcile")
    monkeypatch.setattr(lifecycle, "lock_landed_cost_company", lock)
    monkeypatch.setattr(lifecycle, "reconcile_landed_cost_valuation", reconcile)
    @lifecycle.landed_cost_inventory_operation(date_argument="operation_date")
    async def inner(db, company_id, operation_date, created_by): calls.append("physical")
    @lifecycle.landed_cost_inventory_operation(date_argument="operation_date")
    async def outer(db, company_id, operation_date, created_by):
        await inner(db, company_id, operation_date, created_by)
        calls.append("provenance")
        return 42
    assert await outer(object(), 1, date(2026,9,15), 1) == 42
    assert calls == ["lock", "physical", "provenance", "reconcile"]


@pytest.mark.asyncio
async def test_failed_operation_does_not_reconcile_and_clears_context(monkeypatch):
    lock, reconcile = AsyncMock(), AsyncMock()
    monkeypatch.setattr(lifecycle, "lock_landed_cost_company", lock)
    monkeypatch.setattr(lifecycle, "reconcile_landed_cost_valuation", reconcile)
    @lifecycle.landed_cost_inventory_operation(date_argument="operation_date")
    async def operation(db, company_id, operation_date, created_by): raise ValueError("failed")
    db = object()
    for _ in range(2):
        with pytest.raises(ValueError, match="failed"):
            await operation(db, 1, date(2026,9,15), 1)
    assert lock.await_count == 2
    reconcile.assert_not_awaited()
    assert lifecycle._operations.get() == ()


@pytest.mark.asyncio
async def test_reconciliation_failure_propagates(monkeypatch):
    monkeypatch.setattr(lifecycle, "lock_landed_cost_company", AsyncMock())
    monkeypatch.setattr(lifecycle, "reconcile_landed_cost_valuation", AsyncMock(side_effect=ValueError("invalid provenance")))
    @lifecycle.landed_cost_inventory_operation(date_argument="operation_date")
    async def operation(db, company_id, operation_date, created_by): return 42
    with pytest.raises(ValueError, match="invalid provenance"):
        await operation(object(), 1, date(2026,9,15), 1)
