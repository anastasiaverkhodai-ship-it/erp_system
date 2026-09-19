"""Real accounting proof; private schema and all data roll back on exit."""
from datetime import date
from decimal import Decimal as D
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.models
from app.core.config import settings
from app.core.database import Base
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.document import Document
from app.models.document_line import DocumentLine
from app.models.inventory_cost_entry import InventoryCostEntry
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.moving_average_movement import MovingAverageMovement
from app.models.purchase_landed_cost_event import PurchaseLandedCostEvent
from app.models.purchase_landed_cost_valuation_event import PurchaseLandedCostValuationEvent
from app.models.stock_lot import StockLot
from app.models.stock_lot_consumption import StockLotConsumption
from app.services.accounting_posting import post_journal_entry
from app.services.purchase_landed_cost_capitalization_service import (
    capitalize_purchase_landed_cost, reverse_capitalized_purchase_landed_cost,
)
from app.services.purchase_landed_cost_persistence_service import PurchaseLandedCostPersistenceError
from app.services.purchase_landed_cost_valuation_service import (
    landed_cost_issue_amounts, reconcile_landed_cost_valuation,
)


pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_E2E") != "1", reason="Set RUN_POSTGRES_E2E=1")
DAY = date(2026, 9, 15)


def module_at(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def schema_setup(connection):
    added = {"purchase_landed_cost_capitalizations", "purchase_landed_cost_valuation_events"}
    Base.metadata.create_all(connection, tables=[t for t in Base.metadata.sorted_tables if t.name not in added])
    path = Path(__file__).resolve().parents[1] / "alembic/versions/c0e7a3b5d824_add_landed_cost_capitalization.py"
    module = module_at(path, "landed_capitalization_migration")
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
        module.downgrade()
        module.upgrade()


async def seed_costs(db, method):
    previous = module_at(Path(__file__).with_name("test_purchase_landed_cost_postgresql.py"), "landed_foundation_seed")
    await previous.seed(db)
    for aid, code, kind in ((1, "281", "asset"), (2, "93", "expense"), (3, "631", "liability"), (4, "902", "expense")):
        db.add(Account(id=aid, company_id=1, code=code, name=code, account_type=kind, is_system=True,
                       normal_balance="credit" if kind == "liability" else "debit"))
    db.add(AccountingPeriod(company_id=1, year=2026, month=9, start_date=date(2026, 9, 1),
                            end_date=date(2026, 9, 30), status="open", is_locked=False))
    await db.flush()
    expense = JournalEntry(company_id=1, entry_date=DAY, status="draft", created_by=1, lines=[
        JournalEntryLine(line_no=1, account_id=2, debit=D("200"), credit=0),
        JournalEntryLine(line_no=2, account_id=3, debit=0, credit=D("200")),
    ])
    db.add(expense)
    await db.flush()
    await post_journal_entry(db, 1, expense.id)
    for number, quantity, value in ((1, "1", "5"), (2, "3", "15")):
        if method == "fifo":
            db.add(StockLot(company_id=1, product_id=1, warehouse_id=1,
                            source_document_id=20, source_document_line_id=200 + number,
                            received_date=DAY, original_quantity=D(quantity), remaining_quantity=D(quantity), unit_cost=D("5")))
        else:
            db.add(MovingAverageMovement(company_id=1, document_id=20, document_line_id=200 + number,
                                         product_id=1, warehouse_id=1, movement_type="receipt", movement_date=DAY,
                                         quantity_delta=D(quantity), value_delta=D(value), unit_cost=D("5"),
                                         balance_quantity_after=D("1" if number == 1 else "4"),
                                         balance_value_after=D("5" if number == 1 else "20"), average_unit_cost_after=D("5")))
    await db.flush()
    return expense.lines[0].id


async def add_issue(db, method):
    from app.models.company import Company
    from app.models.stock_balance import StockBalance
    from app.models.moving_average_balance import MovingAverageBalance
    from app.models.accounting_rule import AccountingRule
    from app.models.accounting_rule_line import AccountingRuleLine
    from app.services.document_posting import post_document
    company = await db.get(Company, 1)
    company.inventory_valuation_method = method
    db.add(StockBalance(company_id=1, product_id=1, warehouse_id=1, quantity=D("4")))
    if method == "weighted_average_moving":
        db.add(MovingAverageBalance(company_id=1, product_id=1, warehouse_id=1,
                                    quantity=D("4"), inventory_value=D("20"), average_unit_cost=D("5")))
    rule = AccountingRule(company_id=1, code="LC-ISSUE", name="Issue", document_type="issue", lines=[
        AccountingRuleLine(line_no=1, account_id=4, side="debit", amount_source="inventory_cost"),
        AccountingRuleLine(line_no=2, account_id=1, side="credit", amount_source="inventory_cost"),
    ])
    doc = Document(company_id=1, number="LC-ISSUE", document_type="issue", status="draft", document_date=DAY, created_by=1,
                   lines=[DocumentLine(product_id=1, warehouse_id=1, quantity=D("2"), price=D("5"))])
    db.add_all([rule, doc])
    await db.flush()
    await post_document(db, company_id=1, document_id=doc.id, accounting_rule_id=rule.id, created_by=1)
    return await db.scalar(select(InventoryCostEntry.id).where(InventoryCostEntry.document_id == doc.id))


async def account_net(db, aid):
    return await db.scalar(select(func.coalesce(func.sum(JournalEntryLine.debit - JournalEntryLine.credit), 0))
                           .join(JournalEntry).where(JournalEntry.company_id == 1, JournalEntryLine.account_id == aid))


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["fifo", "weighted_average_moving"])
async def test_capitalization_issue_and_reversal_gl_conserve_source(method):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    schema = "test_capitalization_" + uuid4().hex
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(schema_setup)
                async with AsyncSession(conn, expire_on_commit=False, join_transaction_mode="create_savepoint") as db:
                    line_id = await seed_costs(db, method)
                    args = dict(company_id=1, source_journal_entry_line_id=line_id, request_key="freight-1",
                                trade_document_id=10, warehouse_document_id=20, amount=D("100"), cost_date=DAY, created_by=1)
                    result = await capitalize_purchase_landed_cost(db, **args)
                    assert await account_net(db, 1) == D("100")
                    assert await account_net(db, 2) == D("100")
                    assert await account_net(db, 3) == D("-200")  # Supplier liability is unchanged.
                    assert not (await capitalize_purchase_landed_cost(db, **args)).created
                    with pytest.raises(PurchaseLandedCostPersistenceError, match="unallocated"):
                        await capitalize_purchase_landed_cost(db, **(args | {"request_key": "too-much", "amount": D("101")}))
                    cost_id = await add_issue(db, method)
                    assert (await landed_cost_issue_amounts(db, company_id=1))[cost_id] == D("50")
                    assert await account_net(db, 1) == D("40")  # 50 extra stock minus 10 base issue.
                    assert await account_net(db, 4) == D("60")  # 10 base + 50 landed.
                    assert await account_net(db, 3) == D("-200")
                    assert await reconcile_landed_cost_valuation(db, company_id=1, adjustment_date=DAY, created_by=1) == ()
                    from app.models.warehouse import Warehouse
                    from app.services.warehouse_transfer_history_service import normalize_transfer_target, WarehouseTransferLineTarget
                    from app.services.warehouse_transfer_reconciliation_executor import execute_warehouse_transfer_reconciliation
                    db.add(Warehouse(id=2, company_id=1, name="Destination"))
                    await db.flush()
                    target = normalize_transfer_target(company_id=1, source_warehouse_id=1, destination_warehouse_id=2,
                        transfer_date=DAY, lines=(WarehouseTransferLineTarget(product_id=1, quantity=D("1")),))
                    transfer_args = dict(company_id=1, history_key=str(uuid4()), created_by=1)
                    await execute_warehouse_transfer_reconciliation(db, target=target, adjustment_date=None, **transfer_args)
                    valuations = (await db.execute(select(PurchaseLandedCostValuationEvent).where(
                        PurchaseLandedCostValuationEvent.warehouse_id == 2,
                    ))).scalars().all()
                    assert sum((e.amount * (-1 if e.reversal_of_id else 1) for e in valuations), D(0)) == D("25")
                    assert (await landed_cost_issue_amounts(db, company_id=1))[cost_id] == D("50")
                    assert await account_net(db, 1) == D("40")
                    assert (await execute_warehouse_transfer_reconciliation(db, target=target, adjustment_date=None, **transfer_args)).action.value == "noop"
                    await execute_warehouse_transfer_reconciliation(db, target=None, adjustment_date=DAY, **transfer_args)
                    assert await account_net(db, 1) == D("40")

                    from app.services.accounting_reversal import reverse_journal_entry, AccountingReversalError
                    source_line = await db.get(JournalEntryLine, line_id)
                    with pytest.raises(AccountingReversalError, match="active capitalized"):
                        await reverse_journal_entry(db, company_id=1, journal_entry_id=source_line.journal_entry_id,
                                                    reversal_date=DAY, reversed_by=1)
                    managed_journal = await db.scalar(select(PurchaseLandedCostValuationEvent.journal_entry_id)
                        .join(JournalEntry, JournalEntry.id == PurchaseLandedCostValuationEvent.journal_entry_id)
                        .where(JournalEntry.status == "posted", JournalEntry.reversal_of_id.is_(None)).limit(1))
                    with pytest.raises(AccountingReversalError, match="through its lifecycle"):
                        await reverse_journal_entry(db, company_id=1, journal_entry_id=managed_journal,
                                                    reversal_date=DAY, reversed_by=1)
                    with pytest.raises(PurchaseLandedCostPersistenceError, match="different request"):
                        await capitalize_purchase_landed_cost(db, **(args | {"amount": D("50")}))
                    from fastapi import HTTPException
                    before_count = await db.scalar(select(func.count()).select_from(PurchaseLandedCostEvent))
                    with pytest.raises(HTTPException, match="closed"):
                        async with db.begin_nested():
                            period = await db.scalar(select(AccountingPeriod).where(AccountingPeriod.company_id == 1))
                            period.status = "closed"
                            period.is_locked = True
                            await db.flush()
                            await capitalize_purchase_landed_cost(db, **(args | {"request_key": "closed", "amount": D("50")}))
                    assert await db.scalar(select(func.count()).select_from(PurchaseLandedCostEvent)) == before_count
                    await reverse_capitalized_purchase_landed_cost(db, company_id=1, landed_cost_event_id=result.event.id,
                                                                  reversal_date=DAY, reversed_by=1)
                    assert await account_net(db, 1) == D("-10")
                    assert await account_net(db, 2) == D("200")
                    assert await account_net(db, 4) == D("10")
                    assert await account_net(db, 3) == D("-200")
                    assert (await landed_cost_issue_amounts(db, company_id=1))[cost_id] == 0
                    assert await db.scalar(select(func.count()).select_from(PurchaseLandedCostEvent)) == 2
            finally:
                await tx.rollback()
            assert await conn.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname=:name"), {"name": schema}) == 0
    finally:
        await engine.dispose()


from contextlib import asynccontextmanager


@asynccontextmanager
async def landed_case(method):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    schema = "test_landed_returns_" + uuid4().hex
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(schema_setup)
                async with AsyncSession(conn, expire_on_commit=False, join_transaction_mode="create_savepoint") as db:
                    source = await seed_costs(db, method)
                    args = dict(company_id=1, source_journal_entry_line_id=source, request_key="freight",
                                trade_document_id=10, warehouse_document_id=20, amount=D("100"), cost_date=DAY, created_by=1)
                    result = await capitalize_purchase_landed_cost(db, **args)
                    cost_id = await add_issue(db, method)
                    yield db, args, result.event.id, cost_id
            finally:
                await transaction.rollback()
            assert await conn.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname=:name"), {"name": schema}) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["fifo", "weighted_average_moving"])
async def test_customer_return_restores_landed_cost_through_operational_service(method):
    from app.models.trade_document import TradeDocument
    from app.models.trade_document_line import TradeDocumentLine
    from app.models.trade_fulfillment import TradeFulfillment
    from app.models.trade_fulfillment_line import TradeFulfillmentLine
    from app.models.trade_return_event import TradeReturnEvent
    from app.services.sales_return_operational_service import apply_sales_return_operational_event
    async with landed_case(method) as (db, args, landed_id, cost_id):
        cost = await db.get(InventoryCostEntry, cost_id)
        order = TradeDocument(company_id=1, counterparty_id=1, number="SO-LC", direction="sale", kind="order",
                              status="fulfilled", document_date=DAY, currency_code="UAH", created_by=1)
        db.add(order)
        await db.flush()
        line = TradeDocumentLine(company_id=1, trade_document_id=order.id, line_number=1, product_id=1,
                                 warehouse_id=1, quantity=D("2"), unit_price=D("10"))
        fulfillment = TradeFulfillment(company_id=1, trade_document_id=order.id,
            warehouse_document_id=cost.document_id, warehouse_document_type="issue", created_by=1)
        receipt = Document(company_id=1, number="RETURN-LC", document_type="receipt", status="draft", document_date=DAY,
                           created_by=1, lines=[DocumentLine(product_id=1, warehouse_id=1, quantity=D("1"), price=D("5"))])
        db.add_all([line, fulfillment, receipt])
        await db.flush()
        mapping = TradeFulfillmentLine(company_id=1, fulfillment_id=fulfillment.id, trade_document_id=order.id,
            trade_document_line_id=line.id, warehouse_document_id=cost.document_id,
            warehouse_document_line_id=cost.document_line_id, product_id=1, warehouse_id=1, quantity=D("2"))
        db.add(mapping)
        await db.flush()
        from app.models.invoice_fulfillment_allocation import InvoiceFulfillmentAllocation
        from app.models.sales_recognition_event import SalesRecognitionEvent
        from app.services.sales_recognition_journal_service import generate_and_post_sales_recognition_journal_entry
        for aid, code, kind in ((5, "361", "asset"), (6, "702", "income"), (7, "704", "income")):
            db.add(Account(id=aid, company_id=1, code=code, name=code, account_type=kind, is_system=True,
                           normal_balance="debit" if kind == "asset" or code == "704" else "credit"))
        invoice = TradeDocument(company_id=1, counterparty_id=1, number="SI-LC", direction="sale", kind="invoice",
                                status="confirmed", document_date=DAY, currency_code="UAH", created_by=1)
        db.add(invoice)
        await db.flush()
        invoice_line = TradeDocumentLine(company_id=1, trade_document_id=invoice.id, line_number=1,
            product_id=1, warehouse_id=1, quantity=D("2"), unit_price=D("10"))
        db.add(invoice_line)
        await db.flush()
        allocation = InvoiceFulfillmentAllocation(company_id=1, invoice_id=invoice.id, invoice_line_id=invoice_line.id,
            fulfillment_id=fulfillment.id, fulfillment_line_id=mapping.id, order_id=order.id, order_line_id=line.id,
            product_id=1, quantity=D("2"), status="active", created_by=1)
        db.add(allocation)
        await db.flush()
        recognition = SalesRecognitionEvent(company_id=1, invoice_fulfillment_allocation_id=allocation.id,
            recognition_date=DAY, recognized_quantity=D("2"), recognized_gross_amount=D("20"),
            recognized_tax_amount=D("0"), currency_code="UAH", created_by=1)
        db.add(recognition)
        await db.flush()
        await generate_and_post_sales_recognition_journal_entry(db, event=recognition, created_by=1)
        returned = TradeReturnEvent(company_id=1, direction="sale", original_fulfillment_id=fulfillment.id,
            original_trade_document_id=order.id, original_trade_document_line_id=line.id,
            original_fulfillment_line_id=mapping.id, product_id=1, return_document_id=receipt.id,
            return_document_type="receipt", return_document_line_id=receipt.lines[0].id,
            return_warehouse_id=1, return_date=DAY, returned_quantity=D("1"), created_by=1)
        db.add(returned)
        await db.flush()
        await apply_sales_return_operational_event(db, company_id=1, trade_return_event_id=returned.id, created_by=1)
        assert (await landed_cost_issue_amounts(db, company_id=1))[cost_id] == D("25")
        assert await account_net(db, 1) == D("70")  # 75 landed - 5 net base issue.
        assert await account_net(db, 4) == D("30")  # 25 landed + 5 net base issue.
        assert await account_net(db, 3) == D("-200")

        reversal = TradeReturnEvent(**{name: getattr(returned, name) for name in (
            "company_id", "direction", "original_fulfillment_id", "original_trade_document_id",
            "original_trade_document_line_id", "original_fulfillment_line_id", "product_id",
            "return_document_id", "return_document_type", "return_document_line_id", "return_warehouse_id",
            "return_date", "returned_quantity", "created_by",
        )}, reversal_of_id=returned.id)
        db.add(reversal)
        await db.flush()
        await apply_sales_return_operational_event(db, company_id=1, trade_return_event_id=reversal.id, created_by=1)
        assert (await landed_cost_issue_amounts(db, company_id=1))[cost_id] == D("50")
        assert await account_net(db, 1) == D("40")
        assert await account_net(db, 4) == D("60")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["fifo", "weighted_average_moving"])
async def test_nonrefundable_purchase_return_cost_returns_to_expense(method):
    from app.models.trade_return_event import TradeReturnEvent
    async with landed_case(method) as (db, args, landed_id, cost_id):
        cost = await db.get(InventoryCostEntry, cost_id)
        # The physical ISSUE already exists. Its supplier-return source is the
        # immutable event, just as in the existing recognition lifecycle.
        returned = TradeReturnEvent(company_id=1, direction="purchase", original_fulfillment_id=30,
            original_trade_document_id=10, original_trade_document_line_id=102, original_fulfillment_line_id=302,
            product_id=1, return_document_id=cost.document_id, return_document_type="issue",
            return_document_line_id=cost.document_line_id, return_warehouse_id=1, return_date=DAY,
            returned_quantity=D("2"), created_by=1)
        db.add(returned)
        await db.flush()
        await reconcile_landed_cost_valuation(db, company_id=1, adjustment_date=DAY, created_by=1)
        assert (await landed_cost_issue_amounts(db, company_id=1))[cost_id] == 0
        assert await account_net(db, 2) == D("150")  # Half remains capitalized, half returns to expense.
        assert await account_net(db, 3) == D("-200")
        assert await account_net(db, 4) == D("10")  # Landed cost does not remain in COGS.
        await reverse_capitalized_purchase_landed_cost(db, company_id=1, landed_cost_event_id=landed_id,
                                                      reversal_date=DAY, reversed_by=1)
        assert await account_net(db, 2) == D("200")
        assert await account_net(db, 3) == D("-200")


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [False, True])
async def test_concurrent_requests_cannot_double_capitalize_expense(same_key):
    import asyncio
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    schema = "test_landed_concurrency_" + uuid4().hex
    try:
        # Separate committed private schema is necessary for two independent
        # transactions. Cleanup is restricted to this generated test schema.
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await conn.run_sync(schema_setup)
            async with AsyncSession(conn, expire_on_commit=False) as db:
                source = await seed_costs(db, "fifo")
                await db.flush()
        async def request(key):
            async with engine.connect() as conn:
                async with conn.begin():
                    await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    async with AsyncSession(conn, expire_on_commit=False) as db:
                        result = await capitalize_purchase_landed_cost(db, company_id=1,
                            source_journal_entry_line_id=source, request_key=key, trade_document_id=10,
                            warehouse_document_id=20, amount=D("150"), cost_date=DAY, created_by=1)
                        return result.created
        outcomes = await asyncio.gather(request("first"), request("first" if same_key else "second"), return_exceptions=True)
        if same_key:
            assert sorted(outcomes) == [False, True]
        else:
            assert sum(result is True for result in outcomes) == 1
            assert sum(isinstance(result, PurchaseLandedCostPersistenceError) for result in outcomes) == 1
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                async with AsyncSession(conn) as db:
                    assert await db.scalar(select(func.count()).select_from(PurchaseLandedCostEvent)) == 1
                    assert await account_net(db, 2) == D("50")
                    assert await account_net(db, 3) == D("-200")
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["fifo", "weighted_average_moving"])
async def test_issue_journal_cannot_bypass_inventory_reversal(method):
    from app.services.accounting_reversal import reverse_journal_entry, AccountingReversalError
    from app.services.document_reversal import reverse_document
    async with landed_case(method) as (db, args, landed_id, cost_id):
        cost = await db.get(InventoryCostEntry, cost_id)
        journal_id = await db.scalar(select(JournalEntry.id).where(JournalEntry.document_id == cost.document_id,
                                                                 JournalEntry.reversal_of_id.is_(None)))
        with pytest.raises(AccountingReversalError, match="inventory lifecycle"):
            await reverse_journal_entry(db, company_id=1, journal_entry_id=journal_id, reversal_date=DAY, reversed_by=1)
        await reverse_document(db, company_id=1, document_id=cost.document_id, reversal_date=DAY, reversed_by=1)
        assert (await landed_cost_issue_amounts(db, company_id=1))[cost_id] == 0
        assert await account_net(db, 1) == D("100")
        assert await account_net(db, 4) == 0
        assert await account_net(db, 3) == D("-200")
