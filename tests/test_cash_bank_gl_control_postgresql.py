import os
from decimal import Decimal as D
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool
import app.models
from app.core.config import settings
from app.core.database import Base
from app.services.cash_bank_gl_control_service import reconcile_cash_bank_gl
from test_vat_first_event_postgresql import seed, D1, D4


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')
@pytest.mark.parametrize('destination', ['default', 'bank', 'cash'])
async def test_cash_bank_source_control(destination):
    engine=create_async_engine(settings.database_url,poolclass=NullPool)
    schema='test_cash_control_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                async with AsyncSession(conn,expire_on_commit=False) as db:
                    await seed(db,False,False)
                    from app.models.payment import Payment
                    from app.services.payment_journal_service import generate_and_post_payment_journal_entry
                    payment=await db.get(Payment,1)
                    from app.models.account import Account
                    from app.models.bank_account import BankAccount
                    from app.models.cash_desk import CashDesk
                    if destination != 'default':
                        account = Account(company_id=1, code='301' if destination=='cash' else '313',
                            name='Money', account_type='asset', normal_balance='debit')
                        db.add(account); await db.flush()
                        money = (CashDesk(company_id=1, name='Cash', code='CASH', currency_code='UAH', accounting_account_id=account.id)
                            if destination=='cash' else BankAccount(company_id=1, name='Bank', account_number='CONTROL-ACCOUNT',
                                currency_code='UAH', accounting_account_id=account.id))
                        db.add(money); await db.flush()
                        if destination=='cash': payment.cash_desk_id=money.id
                        else: payment.bank_account_id=money.id
                        await db.flush()
                    await generate_and_post_payment_journal_entry(db,payment=payment,created_by=1)
                    report=await reconcile_cash_bank_gl(db,company_id=1,date_from=D1,date_to=D4)
                    assert report.matched,report
                    assert report.expected_amount==report.posted_amount==D(72)
                    await db.execute(text("UPDATE journal_entry_lines SET debit=CASE WHEN debit>0 THEN 73 ELSE 0 END, credit=CASE WHEN credit>0 THEN 73 ELSE 0 END"))
                    changed=await reconcile_cash_bank_gl(db,company_id=1,date_from=D1,date_to=D4)
                    assert not changed.matched and changed.difference==1
                    assert changed.sources[0].issues[0].code=='journal_accounts_or_amounts_mismatch'
                    await db.execute(text("UPDATE journal_entry_lines SET debit=CASE WHEN debit>0 THEN 72 ELSE 0 END, credit=CASE WHEN credit>0 THEN 72 ELSE 0 END"))
                    from datetime import datetime, timezone
                    from app.services.payment_journal_service import reverse_payment_journal_entry
                    payment.confirmed_at=datetime.combine(D1, datetime.min.time(), timezone.utc)
                    payment.cancelled_at=datetime.combine(D4, datetime.min.time(), timezone.utc)
                    payment.status='cancelled'
                    payment.cancelled_by=1
                    await reverse_payment_journal_entry(db, company_id=1, payment_id=1, reversal_date=D4, reversed_by=1)
                    await db.flush()
                    cancelled=await reconcile_cash_bank_gl(db, company_id=1, date_from=D1, date_to=D4)
                    assert cancelled.matched and cancelled.expected_amount==cancelled.posted_amount==0, cancelled
                    historical=await reconcile_cash_bank_gl(db, company_id=1, date_from=D1, date_to=payment.payment_date)
                    assert historical.matched and historical.expected_amount==72, historical
                    await db.execute(text("UPDATE journal_entries SET entry_date=:day WHERE reversal_of_id IS NOT NULL"), {'day':D1})
                    shifted=await reconcile_cash_bank_gl(db, company_id=1, date_from=D1, date_to=D4)
                    assert not shifted.matched
                    assert any(i.code=='journal_date_mismatch' for source in shifted.sources for i in source.issues)
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
