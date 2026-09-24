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
from app.services.ap_inventory_gl_control_service import reconcile_ap_inventory_gl
from test_vat_first_event_postgresql import seed, D1, D4


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')
async def test_receipt_ap_inventory_and_commercial_bridge():
    engine=create_async_engine(settings.database_url,poolclass=NullPool)
    schema='test_purchase_control_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                async with AsyncSession(conn,expire_on_commit=False) as db:
                    await seed(db,True,False)
                    from app.models.account import Account
                    from app.models.accounting_rule import AccountingRule
                    from app.models.accounting_rule_line import AccountingRuleLine
                    from app.services.document_accounting import generate_and_post_journal_entry_from_document
                    from sqlalchemy import select
                    accounts={a.code:a.id for a in (await db.scalars(select(Account).where(Account.company_id==1))).all()}
                    rule=AccountingRule(company_id=1,code='RECEIPT',name='Receipt',document_type='receipt',lines=[
                        AccountingRuleLine(line_no=1,account_id=accounts['281'],side='debit',amount_source='document_total'),
                        AccountingRuleLine(line_no=2,account_id=accounts['631'],side='credit',amount_source='document_total')])
                    db.add(rule);await db.flush()
                    await generate_and_post_journal_entry_from_document(db,1,1,rule.id,1)
                    for family in ('ap','inventory'):
                        report=await reconcile_ap_inventory_gl(db,company_id=1,date_from=D1,date_to=D4,family=family)
                        assert report.matched,report
                        assert report.expected_amount==report.posted_amount==D(70)
                        if family=='ap':
                            assert report.commercial['open_amount']==120
                            assert report.commercial['commercial_less_economic']==50
                    # Legacy documents kept the rule identity only on their original journal.
                    await db.execute(text('UPDATE documents SET accounting_rule_id=NULL WHERE id=1'))
                    legacy=await reconcile_ap_inventory_gl(db,company_id=1,date_from=D1,date_to=D4,family='ap')
                    assert legacy.matched and legacy.expected_amount==70
                    await db.execute(text("UPDATE journal_entry_lines SET debit=CASE WHEN debit>0 THEN 71 ELSE 0 END, credit=CASE WHEN credit>0 THEN 71 ELSE 0 END"))
                    for family in ('ap','inventory'):
                        changed=await reconcile_ap_inventory_gl(db,company_id=1,date_from=D1,date_to=D4,family=family)
                        assert not changed.matched and changed.difference==1
                        assert changed.sources[0].issues[0].code=='journal_accounts_or_amounts_mismatch'
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
