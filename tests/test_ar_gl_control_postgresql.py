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
from app.services.ar_gl_control_service import reconcile_ar_gl
from app.services.sales_recognition_lifecycle_service import reconcile_sales_recognition_lifecycle_for_invoice_line
from test_vat_first_event_postgresql import seed, D1, D4


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')
async def test_ar_source_control_and_commercial_bridge():
    engine=create_async_engine(settings.database_url,poolclass=NullPool)
    schema='test_ar_control_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                async with AsyncSession(conn,expire_on_commit=False) as db:
                    _,fulfillment=await seed(db,False,False)
                    db.add(fulfillment);await db.flush()
                    await reconcile_sales_recognition_lifecycle_for_invoice_line(db,company_id=1,
                        invoice_id=2,invoice_line_id=2,adjustment_date=D4,created_by=1)
                    report=await reconcile_ar_gl(db,company_id=1,date_from=D1,date_to=D4)
                    assert report.matched,report
                    assert report.bridge.commercial_open==D(120)
                    assert report.bridge.recognized_gross==D(84)
                    assert report.bridge.economic_balance==report.bridge.posted_balance==D(84)
                    assert report.bridge.commercial_less_economic==D(36)
                    # Offsetting manual journals cannot produce a false all-clear.
                    from app.models.account import Account
                    from app.models.journal_entry import JournalEntry
                    from app.models.journal_entry_line import JournalEntryLine
                    from app.services.accounting_posting import post_journal_entry
                    from sqlalchemy import select
                    ids={a.code:a.id for a in (await db.scalars(select(Account).where(Account.company_id==1))).all()}
                    class Restore(Exception):
                        pass
                    with pytest.raises(Restore):
                        async with db.begin_nested():
                            for reverse in (False,True):
                                a,b=(ids['361'],ids['702']) if not reverse else (ids['702'],ids['361'])
                                journal=JournalEntry(company_id=1,entry_date=D4,status='draft',created_by=1,lines=[
                                    JournalEntryLine(line_no=1,account_id=a,debit=1,credit=0),
                                    JournalEntryLine(line_no=2,account_id=b,debit=0,credit=1)])
                                db.add(journal);await db.flush();await post_journal_entry(db,1,journal.id)
                            manual=await reconcile_ar_gl(db,company_id=1,date_from=D1,date_to=D4)
                            assert not manual.matched and manual.bridge.gl_difference==0
                            assert len(manual.unattributed_journal_ids)==2
                            raise Restore()
                    await db.execute(text("UPDATE journal_entry_lines SET debit=CASE WHEN debit>0 THEN 85 ELSE 0 END, credit=CASE WHEN credit>0 THEN 85 ELSE 0 END"))
                    changed=await reconcile_ar_gl(db,company_id=1,date_from=D1,date_to=D4)
                    assert not changed.matched and changed.bridge.gl_difference==1
                    assert changed.sources[0].issues[0].code=='journal_accounts_or_amounts_mismatch'
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
