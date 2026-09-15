from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.purchase_landed_cost_persistence_service as service


DAY = date(2026, 9, 15)


def request(**changes):
    values = dict(company_id=1, trade_document_id=10, warehouse_document_id=20,
                  amount=Decimal("10.00"), currency_code="UAH", cost_date=DAY, created_by=1)
    return values | changes


def line(line_id, quantity):
    return SimpleNamespace(id=line_id, company_id=1, fulfillment_id=30,
                           trade_document_id=10, trade_document_line_id=line_id + 100,
                           warehouse_document_id=20, warehouse_document_line_id=line_id + 200,
                           product_id=5, warehouse_id=6, quantity=Decimal(quantity))


def db_with(*values):
    results = []
    for value in values:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        result.scalars.return_value.all.return_value = value
        results.append(result)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=results)
    async def flush():
        for call in db.add.call_args_list:
            if call.args[0].id is None:
                call.args[0].id = 1000 + db.add.call_args_list.index(call)
    db.flush = AsyncMock(side_effect=flush)
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"amount": Decimal("NaN")}, {"amount": Decimal("Infinity")}, {"amount": Decimal("0")},
    {"amount": Decimal("-1")}, {"amount": Decimal("0.001")}, {"amount": Decimal("1e16")},
    {"amount": True}, {"amount": 1.2}, {"company_id": True}, {"created_by": 0},
    {"currency_code": "uah"}, {"currency_code": "EURO"}, {"cost_date": datetime.now()},
    {"reason_code": " "}, {"reason_code": "x" * 51},
])
async def test_invalid_request_writes_nothing(changes):
    db = db_with()
    with pytest.raises(service.PurchaseLandedCostPersistenceError):
        await service.create_purchase_landed_cost(db, **request(**changes))
    db.execute.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_creation_preserves_provenance_and_rounding(monkeypatch):
    lines = (line(11, "1"), line(12, "1"), line(13, "1"))
    monkeypatch.setattr(service, "_receipt_lines", AsyncMock(return_value=lines))
    db = db_with()
    result = await service.create_purchase_landed_cost(db, **request())
    assert result.created
    assert result.event.amount == Decimal("10.00")
    assert [r.allocated_amount for r in result.allocations] == list(map(Decimal, ("3.33", "3.33", "3.34")))
    assert [r.trade_fulfillment_line_id for r in result.allocations] == [11, 12, 13]
    assert all(r.landed_cost_event_id == result.event.id for r in result.allocations)
    assert all(r.company_id == 1 and r.product_id == 5 for r in result.allocations)
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def receipt_data(**changes):
    order = SimpleNamespace(direction="purchase", kind="order", status="fulfilled", currency_code="UAH", id=10)
    fulfillment = SimpleNamespace(id=30, warehouse_document_type="receipt")
    receipt = SimpleNamespace(id=20, status="posted", document_type="receipt", document_date=DAY)
    rows = [line(11, "1"), line(12, "3")]
    warehouse = [SimpleNamespace(id=r.warehouse_document_line_id, product_id=r.product_id,
                                warehouse_id=r.warehouse_id, quantity=r.quantity) for r in rows]
    for path, value in changes.items():
        obj, attr = path.split("__")
        setattr({"order": order, "fulfillment": fulfillment, "receipt": receipt,
                 "line": rows[0], "warehouse": warehouse[0]}[obj], attr, value)
    return order, fulfillment, rows, receipt, warehouse


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"order__direction": "sale"}, {"order__kind": "invoice"}, {"order__status": "draft"},
    {"order__currency_code": "EUR"}, {"fulfillment__warehouse_document_type": "issue"},
    {"receipt__status": "reversed"}, {"receipt__document_type": "issue"},
    {"receipt__document_date": date(2026, 9, 16)}, {"line__trade_document_id": 999},
    {"line__warehouse_document_id": 999}, {"warehouse__product_id": 999},
    {"warehouse__warehouse_id": 999}, {"warehouse__quantity": Decimal("2")},
    {"line__quantity": Decimal("NaN")}, {"line__quantity": Decimal("Infinity")},
])
async def test_invalid_provenance_rejected_before_writes(changes):
    db = db_with(*receipt_data(**changes))
    with pytest.raises(service.PurchaseLandedCostPersistenceError):
        await service.create_purchase_landed_cost(db, **request())
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_missing_company_source_rejected():
    db = db_with(None)
    with pytest.raises(service.PurchaseLandedCostSourceNotFoundError):
        await service.create_purchase_landed_cost(db, **request(company_id=2))
    db.add.assert_not_called()
    sql = str(db.execute.call_args.args[0])
    assert "company_id" in sql and "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_reversal_is_exact_append_only_and_repeat_is_noop(monkeypatch):
    monkeypatch.setattr(service, "_receipt_lines", AsyncMock(return_value=(line(11, "1"), line(12, "3"))))
    original = await service.create_purchase_landed_cost(db_with(), **request())
    db = db_with(original.event, original.allocations, None)
    result = await service.reverse_purchase_landed_cost(
        db, company_id=1, landed_cost_event_id=original.event.id,
        reversal_date=DAY, reversed_by=2, reason_code="correction",
    )
    result.event.id = 2000
    assert original.event.reversal_of_id is None
    assert result.event.reversal_of_id == original.event.id
    assert result.event.amount == original.event.amount
    assert [r.allocated_amount for r in result.allocations] == [Decimal("2.50"), Decimal("7.50")]
    for before, after in zip(original.allocations, result.allocations):
        for field in service._ALLOCATION_FIELDS:
            assert getattr(before, field) == getattr(after, field)
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    repeated_db = db_with(original.event, original.allocations, result.event, result.allocations)
    repeated = await service.reverse_purchase_landed_cost(
        repeated_db, company_id=1, landed_cost_event_id=original.event.id,
        reversal_date=DAY, reversed_by=2, reason_code="correction",
    )
    assert not repeated.created and repeated.event is result.event
    repeated_db.add.assert_not_called()
    conflict_db = db_with(original.event, original.allocations, result.event, result.allocations)
    with pytest.raises(service.PurchaseLandedCostPersistenceError, match="conflicts"):
        await service.reverse_purchase_landed_cost(
            conflict_db, company_id=1, landed_cost_event_id=original.event.id,
            reversal_date=DAY, reversed_by=3,
        )
    conflict_db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse_id, day", [(9, DAY), (None, date(2026, 9, 14))])
async def test_rejects_reversal_chain_and_backdating(reverse_id, day):
    db = db_with(SimpleNamespace(reversal_of_id=reverse_id, cost_date=DAY))
    with pytest.raises(service.PurchaseLandedCostPersistenceError):
        await service.reverse_purchase_landed_cost(
            db, company_id=1, landed_cost_event_id=100, reversal_date=day, reversed_by=1,
        )
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_receipt_reversal_stops_before_stock_or_accounting(monkeypatch):
    import app.services.document_reversal as reversal
    receipt = SimpleNamespace(id=20, company_id=1, status="posted", document_type="receipt")
    db = db_with(receipt, 30, 100)
    period = AsyncMock()
    monkeypatch.setattr(reversal, "ensure_period_open", period)
    with pytest.raises(reversal.DocumentReversalError, match="active landed costs"):
        await reversal.reverse_document_for_trade_fulfillment(
            db, company_id=1, document_id=20, fulfillment_id=30,
            reversal_date=DAY, reversed_by=1,
        )
    period.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [(), (SimpleNamespace(allocated_amount=Decimal("9"),
    quantity=Decimal("1"), trade_document_id=10),),
    (SimpleNamespace(allocated_amount=Decimal("NaN"), quantity=Decimal("1"), trade_document_id=10),)])
async def test_reversal_rejects_corrupt_history(rows):
    original = SimpleNamespace(id=100, company_id=1, reversal_of_id=None, cost_date=DAY,
                               trade_document_id=10, amount=Decimal("10"))
    db = db_with(original, rows)
    with pytest.raises(service.PurchaseLandedCostPersistenceError, match="do not reconcile"):
        await service.reverse_purchase_landed_cost(
            db, company_id=1, landed_cost_event_id=100, reversal_date=DAY, reversed_by=1,
        )
    db.add.assert_not_called()


@pytest.fixture(autouse=True)
def isolate_landed_cost_boundary(monkeypatch):
    """This module tests the original service body; boundary has separate tests."""
    from unittest.mock import AsyncMock
    import app.services.landed_cost_inventory_lifecycle as boundary
    monkeypatch.setattr(boundary, "lock_landed_cost_company", AsyncMock())
    monkeypatch.setattr(boundary, "reconcile_landed_cost_valuation", AsyncMock())
