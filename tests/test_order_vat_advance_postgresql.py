"""Real PostgreSQL advance-before-invoice, handoff, undo, and atomic failures."""
from datetime import date, datetime, timezone
from decimal import Decimal as D
import os
from uuid import uuid4
import pytest
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool
import app.models
from app.core.config import settings
from app.core.database import Base
from app.models.company import Company
from app.models.user import User
from app.models.counterparty import Counterparty
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine
from app.models.counterparty_open_item import CounterpartyOpenItem
from app.models.payment import Payment
from app.models.tax_calculation import TaxCalculation
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.models.accounting_period import AccountingPeriod
from app.models.order_vat_advance import OrderVatAdvance, OrderVatAdvanceLine, OrderVatAdvanceTransfer
from app.services.company_chart_of_accounts_seeding_service import seed_company_chart_of_accounts
from app.services.invoice_tax_calculation_service import build_invoice_tax_calculation
from app.services.order_vat_advance_service import (
    create_order_vat_advance, transfer_order_advances, undo_order_advance_transfer,
    reverse_order_vat_advance, assert_payment_unbound, assert_order_unbound, OrderVatAdvanceError,
)
from app.services.tax_recognition_lifecycle_service import reconcile_tax_for_invoice, TaxRecognitionLifecycleError

pytestmark = pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='Set RUN_POSTGRES_E2E=1')
DAY = date(2026, 9, 1)


async def seed(db, purchase, mixed=False):
    db.add_all([Company(id=1, name='Advance'), User(id=1, email='advance@example.test', password_hash='unused', first_name='Test', last_name='User')])
    await db.flush()
    db.add_all([Counterparty(id=1, company_id=1, name='Party', counterparty_type='both'),
        Product(id=1, company_id=1, name='Goods', sku='ADV'), Warehouse(id=1, company_id=1, name='Warehouse'),
        AccountingPeriod(company_id=1, year=2026, month=9, start_date=DAY, end_date=date(2026,9,30), status='open')])
    await db.flush()
    await seed_company_chart_of_accounts(session=db, company_id=1)
    order = TradeDocument(id=1, company_id=1, counterparty_id=1, number='ORDER', direction='purchase' if purchase else 'sale',
        kind='order', status='confirmed', document_date=DAY, currency_code='UAH', created_by=1, lines=[])
    order.lines.append(TradeDocumentLine(company_id=1, line_number=1, product_id=1, warehouse_id=1,
        quantity=D(10), unit_price=D(10), tax_rate_code='VAT20', tax_recognition_method='first_event', tax_price_mode='exclusive'))
    if mixed:
        order.lines.append(TradeDocumentLine(company_id=1, line_number=2, product_id=1, warehouse_id=1,
            quantity=D(10), unit_price=D(10), tax_rate_code='VAT7', tax_recognition_method='first_event', tax_price_mode='exclusive'))
    db.add(order)
    db.add(Payment(id=1, company_id=1, counterparty_id=1, number='PAY', direction='outgoing' if purchase else 'incoming',
        status='confirmed', confirmed_at=datetime.now(timezone.utc), payment_date=DAY, currency_code='UAH',
        amount=D('113.50') if mixed else D(72), created_by=1))
    await db.flush()
    return order


async def invoice(db, order):
    inv = TradeDocument(id=2, company_id=1, counterparty_id=1, number='INV', direction=order.direction,
        kind='invoice', status='confirmed', document_date=date(2026,9,3), currency_code='UAH', created_by=1, lines=[])
    for line in order.lines:
        inv.lines.append(TradeDocumentLine(company_id=1, line_number=line.line_number, product_id=line.product_id,
            warehouse_id=line.warehouse_id, quantity=line.quantity, unit_price=line.unit_price,
            tax_rate_code=line.tax_rate_code, tax_recognition_method=line.tax_recognition_method, tax_price_mode=line.tax_price_mode))
    db.add(inv); await db.flush()
    total = D(0)
    for line in inv.lines:
        calc = build_invoice_tax_calculation(document=inv, line=line)
        total += calc.taxable_base + calc.tax_amount
        db.add(calc)
    db.add(CounterpartyOpenItem(company_id=1, trade_document_id=2, counterparty_id=1,
        item_type='payable' if order.direction == 'purchase' else 'receivable', status='open',
        document_date=inv.document_date, due_date=inv.document_date, currency_code='UAH', original_amount=total))
    await db.flush()
    return inv


async def net(db):
    events = list((await db.scalars(select(TaxRecognitionEvent))).all())
    return sum((e.recognized_tax_amount * (-1 if e.reversal_of_id else 1) for e in events), D(0))


@pytest.mark.asyncio
@pytest.mark.parametrize('purchase', [False, True])
@pytest.mark.parametrize('mixed', [False, True])
async def test_preinvoice_advance_transfer_undo_and_reverse(purchase, mixed):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    schema = 'test_order_advance_' + uuid4().hex
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                async with AsyncSession(conn, expire_on_commit=False) as db:
                    order = await seed(db, purchase, mixed)
                    advance = await create_order_vat_advance(db, company_id=1, order_id=1, payment_id=1, created_by=1)
                    expected = D(0) if purchase else D('13.50') if mixed else D(12)
                    assert await net(db) == expected
                    # Independent of an invoice: only the order exists at recognition time.
                    assert await db.scalar(select(func.count()).select_from(TradeDocument)) == 1
                    assert (await create_order_vat_advance(db, company_id=1, order_id=1, payment_id=1, created_by=1)).id == advance.id
                    with pytest.raises(OrderVatAdvanceError):
                        await assert_payment_unbound(db, company_id=1, payment_id=1)
                    with pytest.raises(OrderVatAdvanceError):
                        await assert_order_unbound(db, company_id=1, order_id=1)
                    inv = await invoice(db, order)
                    # No phantom AP/AR on advance; only invoice creates an open item.
                    assert await db.scalar(select(func.count()).select_from(CounterpartyOpenItem)) == 1
                    result = await transfer_order_advances(db, company_id=1, order_id=1, invoice_id=2, created_by=1)
                    assert result[0].status == 'transferred'
                    assert await net(db) == expected
                    assert all(e.recognition_date == DAY for e in (await db.scalars(select(TaxRecognitionEvent))).all())
                    count = await db.scalar(select(func.count()).select_from(TaxRecognitionEvent))
                    await transfer_order_advances(db, company_id=1, order_id=1, invoice_id=2, created_by=1)
                    assert await db.scalar(select(func.count()).select_from(TaxRecognitionEvent)) == count
                    await undo_order_advance_transfer(db, company_id=1, order_id=1, reversed_by=1)
                    assert advance.status == 'active'
                    assert await net(db) == expected
                    assert (await db.scalar(select(OrderVatAdvanceTransfer))).reversed_at is not None
                    await transfer_order_advances(db, company_id=1, order_id=1, invoice_id=2, created_by=1)
                    assert await net(db) == expected
                    await undo_order_advance_transfer(db, company_id=1, order_id=1, reversed_by=1)
                    await reverse_order_vat_advance(db, company_id=1, advance_id=advance.id, reversed_by=1)
                    assert await net(db) == 0
                    await reverse_order_vat_advance(db, company_id=1, advance_id=advance.id, reversed_by=1)
                    assert await net(db) == 0
                    assert await db.scalar(text('SELECT count(*) FROM (SELECT journal_entry_id FROM journal_entry_lines GROUP BY journal_entry_id HAVING sum(debit) != sum(credit)) t')) == 0
            finally:
                await tx.rollback()
            assert await conn.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=:s'), {'s': schema}) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_advance_rejections_are_atomic_and_multiple_payments_keep_dates():
    from fastapi import HTTPException
    from app.services.payment_settlement_service import create_payment_settlement_allocation, PaymentSettlementError
    from app.services.payment_lifecycle_service import cancel_payment, PaymentStatusError
    from app.services.trade_document_lifecycle_service import cancel_sales_order, TradeDocumentLifecycleError
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    schema = 'test_advance_guards_' + uuid4().hex
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                async with AsyncSession(conn, expire_on_commit=False) as db:
                    order = await seed(db, False)
                    for sql, message in [
                        ("UPDATE payments SET amount=121", 'exceeds'),
                        ("UPDATE payments SET direction='outgoing'", 'direction'),
                        ("UPDATE payments SET payment_date='2026-08-31'", 'payment date'),
                    ]:
                        with pytest.raises(OrderVatAdvanceError, match=message):
                            async with db.begin_nested():
                                await db.execute(text(sql))
                                await create_order_vat_advance(db, company_id=1, order_id=1, payment_id=1, created_by=1)
                        assert await db.scalar(select(func.count()).select_from(OrderVatAdvance)) == 0
                        assert await net(db) == 0
                    with pytest.raises(OrderVatAdvanceError, match='Invalid order'):
                        await create_order_vat_advance(db, company_id=999, order_id=1, payment_id=1, created_by=1)
                    with pytest.raises(HTTPException, match='closed'):
                        async with db.begin_nested():
                            await db.execute(text('UPDATE accounting_periods SET is_locked=true'))
                            await create_order_vat_advance(db, company_id=1, order_id=1, payment_id=1, created_by=1)
                    await db.execute(text("UPDATE payments SET payment_date='2026-09-02'"))
                    a = await create_order_vat_advance(db, company_id=1, order_id=1, payment_id=1, created_by=1)
                    # Earlier dated second-entered payment tests chronological handoff.
                    db.add(Payment(id=2, company_id=1, counterparty_id=1, number='PAY2', direction='incoming',
                        status='confirmed', confirmed_at=datetime.now(timezone.utc), payment_date=DAY,
                        currency_code='UAH', amount=D(48), created_by=1))
                    await db.flush()
                    b = await create_order_vat_advance(db, company_id=1, order_id=1, payment_id=2, created_by=1)
                    assert await net(db) == D(20)
                    with pytest.raises(PaymentStatusError, match='advance'):
                        await cancel_payment(db, company_id=1, payment_id=1, cancelled_by=1)
                    with pytest.raises(TradeDocumentLifecycleError, match='advance'):
                        await cancel_sales_order(db, company_id=1, document_id=1)
                    order = await db.scalar(select(TradeDocument).where(TradeDocument.id == 1))
                    await db.refresh(order, ['lines'])
                    inv = await invoice(db, order)
                    item = await db.scalar(select(CounterpartyOpenItem))
                    with pytest.raises(PaymentSettlementError, match='advance'):
                        await create_payment_settlement_allocation(db, company_id=1, payment_id=1,
                            open_item_id=item.id, amount=D(72), created_by=1)
                    for sql, error in [
                        ("UPDATE trade_document_lines SET unit_price=11 WHERE trade_document_id=2", OrderVatAdvanceError),
                        ("UPDATE accounting_periods SET is_locked=true", HTTPException),
                    ]:
                        with pytest.raises(error):
                            async with db.begin_nested():
                                await db.execute(text(sql))
                                await transfer_order_advances(db, company_id=1, order_id=1, invoice_id=2, created_by=1)
                        await db.refresh(a)
                        assert a.status == 'active'
                        assert await net(db) == D(20)
                    await transfer_order_advances(db, company_id=1, order_id=1, invoice_id=2, created_by=1)
                    assert await net(db) == D(20)
                    async def dated_net():
                        return dict((await db.execute(text('SELECT recognition_date, sum(CASE WHEN reversal_of_id IS NULL THEN recognized_tax_amount ELSE -recognized_tax_amount END) FROM tax_recognition_events GROUP BY recognition_date'))).all())
                    assert await dated_net() == {DAY: D(8), date(2026,9,2): D(12)}
                    with pytest.raises(HTTPException, match='closed'):
                        async with db.begin_nested():
                            await db.execute(text('UPDATE accounting_periods SET is_locked=true'))
                            await undo_order_advance_transfer(db, company_id=1, order_id=1, reversed_by=1)
                    await undo_order_advance_transfer(db, company_id=1, order_id=1, reversed_by=1)
                    assert await dated_net() == {DAY: D(8), date(2026,9,2): D(12)}
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
