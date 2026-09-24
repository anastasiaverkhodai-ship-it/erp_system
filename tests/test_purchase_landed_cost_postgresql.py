"""Opt-in PostgreSQL proof in a new schema, with DDL and data rolled back.

RUN_POSTGRES_E2E=1 python -m pytest -q tests/test_purchase_landed_cost_postgresql.py
Uses the configured database, but never migrates its application schema.
"""

from datetime import date
from decimal import Decimal
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import Base
from app.models.company import Company
from app.models.counterparty import Counterparty
from app.models.document import Document
from app.models.document_line import DocumentLine
from app.models.product import Product
from app.models.purchase_landed_cost_event import PurchaseLandedCostEvent
from app.models.purchase_landed_cost_allocation_event import PurchaseLandedCostAllocationEvent
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine
from app.models.trade_fulfillment import TradeFulfillment
from app.models.trade_fulfillment_line import TradeFulfillmentLine
from app.models.user import User
from app.models.warehouse import Warehouse
from app.services.document_reversal import DocumentReversalError, reverse_document_for_trade_fulfillment
from app.services.purchase_landed_cost_persistence_service import (
    PurchaseLandedCostSourceNotFoundError, create_purchase_landed_cost,
    has_active_purchase_landed_costs, reverse_purchase_landed_cost,
)


pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_E2E") != "1", reason="Set RUN_POSTGRES_E2E=1")
DAY = date(2026, 9, 15)
LANDED = {"purchase_landed_cost_events", "purchase_landed_cost_allocation_events"}


def setup_schema(connection):
    names = {"trade_fulfillment_lines", "opening_balance_details"}
    while True:
        expanded = names | {fk.column.table.name for n in names for fk in Base.metadata.tables[n].foreign_keys}
        if names == expanded:
            break
        names = expanded
    Base.metadata.create_all(connection, tables=[Base.metadata.tables[n] for n in sorted(names)])
    migration_path = Path(__file__).resolve().parents[1] / "alembic/versions/b9d6f2a4c713_add_purchase_landed_cost_foundation.py"
    spec = importlib.util.spec_from_file_location("landed_migration_test", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.downgrade()
        migration.upgrade()

    def include_object(obj, name, type_, reflected, compare_to):
        if type_ == "table":
            return name in LANDED
        return getattr(getattr(obj, "table", None), "name", None) in LANDED

    context = MigrationContext.configure(connection, opts={"include_object": include_object})
    assert compare_metadata(context, Base.metadata) == []


async def seed(db):
    db.add_all([
        Company(id=1, name="Landed test"), Company(id=2, name="Other tenant"),
        User(id=1, email="landed@example.test", password_hash="unused", first_name="Test", last_name="User"),
    ])
    await db.flush()
    db.add_all([Counterparty(id=1, company_id=1, name="Supplier"),
                Product(id=1, company_id=1, name="Product", sku="LC-1"),
                Warehouse(id=1, company_id=1, name="Warehouse")])
    await db.flush()
    db.add_all([
        TradeDocument(id=10, company_id=1, counterparty_id=1, number="PO-LC", direction="purchase",
                      kind="order", status="fulfilled", document_date=DAY, currency_code="UAH", created_by=1),
        Document(id=20, company_id=1, number="RC-LC", document_type="receipt", status="posted",
                 document_date=DAY, created_by=1),
    ])
    await db.flush()
    for number, quantity in ((1, "1"), (2, "3")):
        db.add(TradeDocumentLine(id=100 + number, company_id=1, trade_document_id=10,
                                line_number=number, product_id=1, warehouse_id=1,
                                quantity=Decimal(quantity), unit_price=Decimal("5")))
        db.add(DocumentLine(id=200 + number, document_id=20, product_id=1, warehouse_id=1,
                            quantity=Decimal(quantity), price=Decimal("5")))
    db.add(TradeFulfillment(id=30, company_id=1, trade_document_id=10, warehouse_document_id=20,
                            warehouse_document_type="receipt", created_by=1))
    await db.flush()
    for number, quantity in ((1, "1"), (2, "3")):
        db.add(TradeFulfillmentLine(id=300 + number, company_id=1, fulfillment_id=30,
                                   trade_document_id=10, trade_document_line_id=100 + number,
                                   warehouse_document_id=20, warehouse_document_line_id=200 + number,
                                   product_id=1, warehouse_id=1, quantity=Decimal(quantity)))
    await db.flush()


@pytest.mark.asyncio
async def test_migration_lifecycle_constraints_and_caller_rollback(monkeypatch):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    schema = "test_landed_" + uuid4().hex
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                # Schema name is generated locally from a UUID, never user input.
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.run_sync(setup_schema)
                async with AsyncSession(bind=connection, expire_on_commit=False,
                                        join_transaction_mode="create_savepoint") as db:
                    await seed(db)
                    await db.commit()  # Releases only this session's savepoint.
                    args = dict(company_id=1, trade_document_id=10, warehouse_document_id=20,
                                amount=Decimal("100.00"), currency_code="UAH", cost_date=DAY, created_by=1)
                    with pytest.raises(PurchaseLandedCostSourceNotFoundError):
                        await create_purchase_landed_cost(db, **(args | {"company_id": 2}))
                    original = await create_purchase_landed_cost(db, **args)
                    original_id = original.event.id
                    assert [r.allocated_amount for r in original.allocations] == [Decimal("25"), Decimal("75")]
                    assert await has_active_purchase_landed_costs(db, company_id=1, warehouse_document_id=20)
                    with pytest.raises(DocumentReversalError, match="active landed costs"):
                        await reverse_document_for_trade_fulfillment(
                            db, company_id=1, document_id=20, fulfillment_id=30,
                            reversal_date=DAY, reversed_by=1,
                        )
                    reverse_args = dict(company_id=1, landed_cost_event_id=original_id,
                                        reversal_date=DAY, reversed_by=1)
                    reversal = await reverse_purchase_landed_cost(db, **reverse_args)
                    assert reversal.event.reversal_of_id == original_id
                    assert not await has_active_purchase_landed_costs(db, company_id=1, warehouse_document_id=20)
                    assert not (await reverse_purchase_landed_cost(db, **reverse_args)).created
                    assert await db.scalar(select(func.count()).select_from(PurchaseLandedCostEvent)) == 2
                    assert await db.scalar(select(func.count()).select_from(PurchaseLandedCostAllocationEvent)) == 4
                    assert await db.scalar(select(Document.status).where(Document.id == 20)) == "posted"
                    assert await db.scalar(select(func.sum(DocumentLine.quantity))) == Decimal("4")

                    # Real PostgreSQL uniqueness prevents a second reversal even
                    # if a caller bypasses the service's serialized source lock.
                    with pytest.raises(IntegrityError):
                        async with db.begin_nested():
                            db.add(PurchaseLandedCostEvent(
                                **args, reversal_of_id=original_id,
                            ))
                            await db.flush()
                    with pytest.raises(IntegrityError):
                        async with db.begin_nested():
                            db.add(PurchaseLandedCostEvent(
                                **(args | {"company_id": 2}), reversal_of_id=original_id,
                            ))
                            await db.flush()
                    with pytest.raises(IntegrityError):
                        async with db.begin_nested():
                            row = original.allocations[0]
                            db.add(PurchaseLandedCostAllocationEvent(
                                company_id=1, landed_cost_event_id=original_id,
                                trade_fulfillment_line_id=99999, trade_document_id=10,
                                trade_document_line_id=row.trade_document_line_id,
                                warehouse_document_line_id=row.warehouse_document_line_id,
                                product_id=1, warehouse_id=1, quantity=1, allocated_amount=1,
                            ))
                            await db.flush()
                    # The service must not commit either of its two flushes.
                    await db.rollback()
                    assert await db.scalar(select(func.count()).select_from(PurchaseLandedCostEvent)) == 0
                    assert await db.scalar(select(func.count()).select_from(PurchaseLandedCostAllocationEvent)) == 0
                    assert await db.scalar(select(func.count()).select_from(Document)) == 1

                    # A failure after the source flush must also be recoverable
                    # by the caller, with no orphan source left behind.
                    real_flush = db.flush
                    flush_count = 0

                    async def fail_allocation_flush(*a, **kw):
                        nonlocal flush_count
                        flush_count += 1
                        if flush_count == 2:
                            raise RuntimeError("allocation flush failed")
                        return await real_flush(*a, **kw)

                    with monkeypatch.context() as patch:
                        patch.setattr(db, "flush", fail_allocation_flush)
                        with pytest.raises(RuntimeError, match="allocation flush failed"):
                            await create_purchase_landed_cost(db, **args)
                    await db.rollback()
                    assert await db.scalar(select(func.count()).select_from(PurchaseLandedCostEvent)) == 0
                    assert await db.scalar(select(func.count()).select_from(PurchaseLandedCostAllocationEvent)) == 0
            finally:
                await transaction.rollback()
            assert await connection.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname = :schema"),
                                           {"schema": schema}) == 0
    finally:
        await engine.dispose()
