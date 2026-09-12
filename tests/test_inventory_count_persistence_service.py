from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql

from app.services.inventory_count_persistence_service import (
    InventoryCountPersistenceError,
    append_inventory_count_event,
    lock_inventory_count_history,
)


KEY = "33333333-3333-4333-8333-333333333333"


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class FakeResult:
    def __init__(self, rows=()):
        self._rows = rows

    def scalars(self):
        return FakeScalarResult(
            self._rows
        )


class FakeAsyncSession:
    def __init__(self, rows=()):
        self.rows = tuple(rows)
        self.statements = []
        self.added = []
        self.flush_count = 0

    async def execute(self, statement):
        self.statements.append(
            statement
        )
        return FakeResult(
            self.rows
        )

    def add(self, obj):
        self.added.append(
            obj
        )

    async def flush(self):
        self.flush_count += 1


def compile_sql(statement):
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )


@pytest.mark.asyncio
async def test_count_history_lock_is_chain_scoped_for_update():
    db = FakeAsyncSession()

    rows = await lock_inventory_count_history(
        db,
        company_id=7,
        history_key=KEY,
    )

    assert rows == ()

    sql = compile_sql(
        db.statements[0]
    )

    assert "inventory_count_events.company_id = 7" in sql
    assert (
        "inventory_count_events.history_key = "
        f"'{KEY}'"
    ) in sql
    assert "ORDER BY inventory_count_events.id" in sql
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_count_original_append_flushes_without_commit():
    db = FakeAsyncSession()

    event = await append_inventory_count_event(
        db,
        company_id=1,
        history_key=KEY,
        product_id=100,
        warehouse_id=10,
        count_date=date(2026, 9, 10),
        expected_quantity=Decimal("10"),
        counted_quantity=Decimal("8"),
        created_by=5,
    )

    assert db.added == [event]
    assert db.flush_count == 1

    assert event.history_key == KEY
    assert event.expected_quantity == Decimal("10")
    assert event.counted_quantity == Decimal("8")
    assert event.reversal_of_id is None


@pytest.mark.asyncio
async def test_count_reversal_append_preserves_snapshot_and_chain():
    db = FakeAsyncSession()

    event = await append_inventory_count_event(
        db,
        company_id=1,
        history_key=KEY,
        product_id=100,
        warehouse_id=10,
        count_date=date(2026, 9, 11),
        expected_quantity=Decimal("10"),
        counted_quantity=Decimal("8"),
        created_by=5,
        reversal_of_id=99,
    )

    assert event.history_key == KEY
    assert event.reversal_of_id == 99
    assert event.expected_quantity == Decimal("10")
    assert event.counted_quantity == Decimal("8")


@pytest.mark.asyncio
async def test_zero_variance_snapshot_is_valid():
    db = FakeAsyncSession()

    event = await append_inventory_count_event(
        db,
        company_id=1,
        history_key=KEY,
        product_id=100,
        warehouse_id=10,
        count_date=date(2026, 9, 10),
        expected_quantity=Decimal("10"),
        counted_quantity=Decimal("10"),
        created_by=5,
    )

    assert event.variance_quantity == Decimal("0")
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_count_rejects_negative_expected_quantity():
    db = FakeAsyncSession()

    with pytest.raises(
        InventoryCountPersistenceError,
        match="cannot be negative",
    ):
        await append_inventory_count_event(
            db,
            company_id=1,
            history_key=KEY,
            product_id=100,
            warehouse_id=10,
            count_date=date(2026, 9, 10),
            expected_quantity=Decimal("-1"),
            counted_quantity=Decimal("0"),
            created_by=5,
        )

    assert db.added == []
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_count_rejects_invalid_history_key():
    db = FakeAsyncSession()

    with pytest.raises(
        ValueError,
        match="valid UUID",
    ):
        await append_inventory_count_event(
            db,
            company_id=1,
            history_key="bad-key",
            product_id=100,
            warehouse_id=10,
            count_date=date(2026, 9, 10),
            expected_quantity=Decimal("1"),
            counted_quantity=Decimal("1"),
            created_by=5,
        )
