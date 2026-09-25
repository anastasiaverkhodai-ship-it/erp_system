"""Verify month closing against a disposable full schema copy, never source data.

Run from repository root with PYTHONPATH=. and --output <local evidence.json>.
The copied schema has its own sequences, constraints and search_path. It is dropped
in finally. This is a month-close rehearsal, not authorization to close a live year.
"""
import argparse
import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Enum, MetaData, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.schema import AddConstraint
from sqlalchemy.pool import NullPool

import app.models
from app.core.config import settings
from app.core.database import Base
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.api.v1.accounting_periods import close_accounting_period, reopen_accounting_period
from app.services.accounting_posting import post_journal_entry
from app.services.general_ledger_service import get_general_ledger, get_account_card
from app.services.trial_balance_service import get_trial_balance
from app.services.accounting_control_service import get_consolidated_accounting_controls


async def fingerprint(connection, schema):
    q=connection.dialect.identifier_preparer.quote
    values={}
    for name in sorted(Base.metadata.tables):
        rows=list((await connection.scalars(text(
            f'SELECT to_jsonb(t) FROM {q(schema)}.{q(name)} t'))).all())
        canonical=sorted(json.dumps(row,sort_keys=True,default=str) for row in rows)
        values[name]=hashlib.sha256(json.dumps(canonical).encode()).hexdigest()
    return values


async def run(output):
    # LIKE retains references to source enum types. Qualify native enum binds
    # explicitly instead of adding public to search_path (which could expose
    # source tables to accidental fallback). Enum types are read-only here.
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, Enum) and column.type.native_enum and column.type.schema is None:
                column.type.schema='public'
    schema='pre12_verify_'+uuid4().hex
    admin=create_async_engine(settings.database_url,poolclass=NullPool)
    copy=create_async_engine(settings.database_url,poolclass=NullPool,
        connect_args={'server_settings':{'search_path':schema}})
    evidence={'schema':schema,'source_schema':'public','company_id':1,'period':'2026-08'}
    try:
        async with admin.begin() as conn:
            await conn.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
            evidence['source_before']=await fingerprint(conn,'public')
            q=conn.dialect.identifier_preparer.quote
            await conn.execute(text(f'CREATE SCHEMA {q(schema)}'))
            counts={}
            for name,table in sorted(Base.metadata.tables.items()):
                target=f'{q(schema)}.{q(name)}'
                await conn.execute(text(f'CREATE TABLE {target} (LIKE public.{q(name)} INCLUDING ALL)'))
                await conn.execute(text(f'INSERT INTO {target} SELECT * FROM public.{q(name)}'))
                counts[name]=await conn.scalar(text(f'SELECT count(*) FROM {target}'))
                # LIKE preserves SERIAL defaults pointing at public sequences: replace them.
                for col in table.columns:
                    sequence=await conn.scalar(text('SELECT pg_get_serial_sequence(:table,:column)'),
                        {'table':f'public.{name}','column':col.name})
                    if sequence:
                        sequence_name=f'{name}_{col.name}_copy_seq'
                        quoted=f'{q(schema)}.{q(sequence_name)}'
                        await conn.execute(text(f'CREATE SEQUENCE {quoted}'))
                        await conn.execute(text(f"ALTER TABLE {target} ALTER COLUMN {q(col.name)} SET DEFAULT nextval('{quoted}'::regclass)"))
                        await conn.execute(text(f'ALTER SEQUENCE {quoted} OWNED BY {target}.{q(col.name)}'))
                        maximum=await conn.scalar(text(f'SELECT max({q(col.name)}) FROM {target}'))
                        await conn.execute(text('SELECT setval(CAST(:sequence AS regclass),:value,:called)'),
                            {'sequence':quoted,'value':maximum or 1,'called':maximum is not None})
            metadata=MetaData()
            for table in Base.metadata.tables.values():
                table.to_metadata(metadata,schema=schema)
            def constraints(sync):
                for table in metadata.tables.values():
                    for constraint in table.foreign_key_constraints:
                        sync.execute(AddConstraint(constraint))
            await conn.run_sync(constraints)
            evidence['copied_tables']=len(counts)
            evidence['copied_rows']=sum(counts.values())
            evidence['copy_initial']=await fingerprint(conn,schema)
            assert evidence['copy_initial']==evidence['source_before']
        async with AsyncSession(copy,expire_on_commit=False) as db:
            assert await db.scalar(text('SELECT current_schema()'))==schema
            period=await db.scalar(select(AccountingPeriod).where(AccountingPeriod.company_id==1,
                AccountingPeriod.year==2026,AccountingPeriod.month==8))
            assert period.status=='open' and not period.is_locked
            assert await db.scalar(text("SELECT count(*) FROM journal_entries WHERE company_id=1 AND status='draft'"))==0
            ids={a.code:a.id for a in (await db.scalars(select(Account).where(Account.company_id==1))).all()}
            async def reports():
                args=dict(company_id=1,date_from=date(2026,8,1),date_to=date(2026,8,31))
                trial=await get_trial_balance(db,**args)
                gl=await get_general_ledger(db,**args)
                assert gl.period_debit==trial.total_period_debit
                assert gl.period_credit==trial.total_period_credit
                cards={}
                for code in ('281','631'):
                    card=await get_account_card(db,account_id=ids[code],**args)
                    cards[code]=card.model_dump(mode='json')
                return dict(trial=trial.model_dump(mode='json'),gl=gl.model_dump(mode='json'),cards=cards)
            before=await reports()
            await close_accounting_period(1,period.id,db=db)
            try:
                await close_accounting_period(1,period.id,db=db)
            except HTTPException as exc:
                assert exc.status_code==409
            else:
                raise AssertionError('Duplicate closing was not rejected')
            probe=JournalEntry(company_id=1,entry_date=date(2026,8,18),status='draft',created_by=1,
                description='Disposable pre12 closed-period probe',lines=[
                    JournalEntryLine(line_no=1,account_id=ids['281'],debit=1,credit=0),
                    JournalEntryLine(line_no=2,account_id=ids['631'],debit=0,credit=1)])
            db.add(probe);await db.flush()
            try:
                await post_journal_entry(db,1,probe.id)
            except HTTPException as exc:
                assert exc.status_code==409 and 'closed' in str(exc.detail).lower()
            else:
                raise AssertionError('Closed period accepted posting')
            await db.delete(probe);await db.commit()
            await reopen_accounting_period(1,period.id,db=db)
            assert period.status=='open' and not period.is_locked
            await close_accounting_period(1,period.id,db=db)
            assert await reports()==before
            controls=await get_consolidated_accounting_controls(db,company_id=1,
                date_from=date(2026,1,1),date_to=date(2026,12,31))
            assert all(f.difference==0 for f in controls.families)
            evidence['checks']=['full_copy_fingerprint','foreign_keys_restored','close',
                'duplicate_close_rejected','posting_into_closed_period_rejected','reopen','reclose',
                'gl_trial_cards_unchanged','all_control_amount_differences_zero']
            evidence['controls']=controls.model_dump(mode='json')
            evidence['trial_balance']=before['trial']
        async with admin.connect() as conn:
            evidence['source_after']=await fingerprint(conn,'public')
            assert evidence['source_before']==evidence['source_after'], 'Source changed during rehearsal; review concurrent activity'
        evidence['passed']=True
    finally:
        await copy.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
    Path(output).write_text(json.dumps(evidence,indent=2,ensure_ascii=False,default=str))
    print(json.dumps({k:evidence[k] for k in ('passed','copied_tables','copied_rows','checks')},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    asyncio.run(run(parser.parse_args().output))
