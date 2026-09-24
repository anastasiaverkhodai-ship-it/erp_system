"""A single GL snapshot detects corruption, including a shifted period boundary."""
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool
import app.models
from app.core.config import settings
from app.core.database import Base
from app.models.company import Company
from app.services.company_chart_of_accounts_seeding_service import seed_company_chart_of_accounts
from app.services.input_vat_gl_reconciliation_service import reconcile_input_vat_gl
from app.services.input_tax_recognition_reconciliation_service import reconcile_input_tax_calculation_from_active_sources
from app.services.tax_recognition_lifecycle_service import _post_created_input_vat_recognition_events
from test_vat_first_event_postgresql import seed, D1, D4


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')
async def test_real_gl_anomalies_period_boundary_company_scope_and_read_only():
    engine=create_async_engine(settings.database_url,poolclass=NullPool)
    schema='test_input_control_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                async with AsyncSession(conn,expire_on_commit=False) as db:
                    payment,_=await seed(db,True,True)
                    db.add(payment); await db.flush()
                    result=await reconcile_input_tax_calculation_from_active_sources(db,company_id=1,
                        tax_calculation_id=1,adjustment_date=D1,created_by=1)
                    await _post_created_input_vat_recognition_events(db,result=result,created_by=1)
                    await db.flush()
                    async def control(start=D1,end=D4,company=1):
                        return await reconcile_input_vat_gl(db,company_id=company,date_from=start,date_to=end)
                    initial=await control()
                    assert initial.matched and initial.expected_input_vat==Decimal(12)
                    class RollbackFixture(Exception):
                        pass
                    for sql,code,difference in [
                        ("UPDATE journal_entries SET entry_date='2026-10-01'",'journal_date_mismatch',Decimal(-12)),
                        ("UPDATE journal_entry_lines SET debit=CASE WHEN debit>0 THEN 13 ELSE 0 END, credit=CASE WHEN credit>0 THEN 13 ELSE 0 END",'journal_accounts_or_amounts_mismatch',Decimal(1)),
                        ("UPDATE journal_entries SET status='draft'",'journal_not_posted',Decimal(-12)),
                    ]:
                        with pytest.raises(RollbackFixture):
                            async with db.begin_nested():
                                await db.execute(text(sql))
                                report=await control()
                                assert not report.matched and report.difference==difference
                                assert code in [i.code for i in report.issues]
                                if code=='journal_date_mismatch':
                                    later=await control(date(2026,10,1),date(2026,10,31))
                                    assert later.expected_input_vat==0 and later.posted_input_vat==12
                                    assert not later.matched
                                raise RollbackFixture()
                        assert (await control()).matched
                    with pytest.raises(RollbackFixture):
                        async with db.begin_nested():
                            await db.execute(text('DELETE FROM journal_entry_lines'))
                            await db.execute(text('DELETE FROM journal_entries'))
                            report=await control()
                            assert report.issues[0].code=='missing_journal'
                            assert report.difference==-12
                            raise RollbackFixture()
                    db.add(Company(id=2,name='Other company')); await db.flush()
                    await seed_company_chart_of_accounts(session=db,company_id=2)
                    other=await control(company=2)
                    assert other.matched and other.event_count==0 and other.posted_input_vat==0
                    await db.execute(text("UPDATE accounting_periods SET status='closed', is_locked=true"))
                    assert (await control()).matched  # Closed period remains readable, never repaired by GET.
                    assert await conn.scalar(text('SELECT count(*) FROM tax_recognition_events'))==1
                    assert await conn.scalar(text('SELECT count(*) FROM journal_entries'))==1
            finally:
                await tx.rollback()
            assert await conn.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=:s'),{'s':schema})==0
    finally:
        await engine.dispose()
