from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql

from app.models.document import DocumentType
from app.services.warehouse_transfer_persistence_service import (
    WarehouseTransferPersistenceError,
    append_warehouse_transfer_event,
    append_warehouse_transfer_line,
    load_warehouse_transfer_lines,
    lock_warehouse_transfer_history,
)


KEY = "11111111-1111-4111-8111-111111111111"


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
async def test_transfer_history_lock_is_chain_scoped_for_update():
    db = FakeAsyncSession()

    result = await lock_warehouse_transfer_history(
        db,
        company_id=7,
        history_key=KEY,
    )

    assert result == ()
    assert len(db.statements) == 1

    sql = compile_sql(
        db.statements[0]
    )

    assert "warehouse_transfer_events.company_id = 7" in sql
    assert (
        "warehouse_transfer_events.history_key = "
        f"'{KEY}'"
    ) in sql
    assert "ORDER BY warehouse_transfer_events.id" in sql
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_transfer_event_append_flushes_without_commit():
    db = FakeAsyncSession()

    event = await append_warehouse_transfer_event(
        db,
        company_id=1,
        history_key=KEY,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        transfer_date=date(2026, 9, 10),
        created_by=5,
    )

    assert db.added == [event]
    assert db.flush_count == 1

    assert event.company_id == 1
    assert event.history_key == KEY
    assert event.reversal_of_id is None


@pytest.mark.asyncio
async def test_transfer_reversal_append_preserves_chain():
    db = FakeAsyncSession()

    event = await append_warehouse_transfer_event(
        db,
        company_id=1,
        history_key=KEY,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        transfer_date=date(2026, 9, 11),
        created_by=5,
        reversal_of_id=99,
    )

    assert event.history_key == KEY
    assert event.reversal_of_id == 99


@pytest.mark.asyncio
async def test_transfer_line_is_exact_issue_receipt_provenance():
    db = FakeAsyncSession()

    line = await append_warehouse_transfer_line(
        db,
        company_id=1,
        transfer_event_id=2,
        product_id=100,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        quantity=Decimal("5.2500"),
        issue_document_id=30,
        issue_document_line_id=31,
        receipt_document_id=40,
    )

    assert db.added == [line]
    assert db.flush_count == 1

    assert line.quantity == Decimal("5.2500")
    assert line.issue_document_type == DocumentType.ISSUE
    assert (
        line.receipt_document_type
        == DocumentType.RECEIPT
    )


@pytest.mark.asyncio
async def test_transfer_line_rejects_same_warehouse():
    db = FakeAsyncSession()

    with pytest.raises(
        WarehouseTransferPersistenceError,
        match="must differ",
    ):
        await append_warehouse_transfer_line(
            db,
            company_id=1,
            transfer_event_id=2,
            product_id=100,
            source_warehouse_id=10,
            destination_warehouse_id=10,
            quantity=Decimal("1"),
            issue_document_id=30,
            issue_document_line_id=31,
            receipt_document_id=40,
        )

    assert db.added == []
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_transfer_line_rejects_nonpositive_quantity():
    db = FakeAsyncSession()

    with pytest.raises(
        WarehouseTransferPersistenceError,
        match="greater than zero",
    ):
        await append_warehouse_transfer_line(
            db,
            company_id=1,
            transfer_event_id=2,
            product_id=100,
            source_warehouse_id=10,
            destination_warehouse_id=20,
            quantity=Decimal("0"),
            issue_document_id=30,
            issue_document_line_id=31,
            receipt_document_id=40,
        )


@pytest.mark.asyncio
async def test_empty_transfer_line_loader_does_not_query():
    db = FakeAsyncSession()

    rows = await load_warehouse_transfer_lines(
        db,
        transfer_event_ids=(),
    )

    assert rows == ()
    assert db.statements == []


@pytest.mark.asyncio
async def test_transfer_line_loader_scopes_event_ids():
    db = FakeAsyncSession()

    await load_warehouse_transfer_lines(
        db,
        transfer_event_ids=(11, 12),
    )

    sql = compile_sql(
        db.statements[0]
    )

    assert (
        "warehouse_transfer_lines.transfer_event_id "
        "IN (11, 12)"
    ) in sql
