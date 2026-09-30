"""Run PostgreSQL tests in a disposable, constraint-preserving data copy.

Use: PYTHONPATH=. .venv/bin/python scripts/run_postgresql_regression_copy.py tests
The configured source database is read-only. Test writes use a separate database,
which is removed on success or failure. Requires CREATE DATABASE permission.
"""
import asyncio, json, os, sys, uuid
from sqlalchemy import text, MetaData
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import DropConstraint, AddConstraint
from sqlalchemy.pool import NullPool
import app.models
from app.core.config import settings
from app.core.database import Base

async def main():
    name='erp_fa_copy_'+uuid.uuid4().hex[:12]
    url=make_url(settings.database_url)
    source=create_async_engine(url,poolclass=NullPool)
    admin=create_async_engine(url.set(database='postgres'),isolation_level='AUTOCOMMIT',poolclass=NullPool)
    target=create_async_engine(url.set(database=name),poolclass=NullPool)
    created=False
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'));created=True
        async with target.begin() as dest:
            await dest.run_sync(Base.metadata.create_all)
            constraints=[]
            def drop_fks(sync):
                metadata=MetaData();metadata.reflect(bind=sync)
                for table in metadata.tables.values():
                    for constraint in table.foreign_key_constraints:
                        constraints.append(constraint);sync.execute(DropConstraint(constraint))
            await dest.run_sync(drop_fks)
            count=0
            async with source.connect() as src:
                await src.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
                q=dest.dialect.identifier_preparer.quote
                for name_table in sorted(Base.metadata.tables):
                    table=q(name_table)
                    # Keep PostgreSQL's numeric JSON text intact: decoding and
                    # re-encoding monetary values through float can lose precision.
                    rows_json=await src.scalar(text(f"SELECT COALESCE(json_agg(t), '[]'::json)::text FROM {table} t"))
                    rows=json.loads(rows_json)
                    if rows:
                        await dest.execute(text(f'INSERT INTO {table} SELECT * FROM json_populate_recordset(NULL::{table},CAST(:data AS json))'),{'data':rows_json})
                        count+=len(rows)
                    for column in Base.metadata.tables[name_table].primary_key.columns:
                        if column.name!='id':continue
                        sequence=await dest.scalar(text('SELECT pg_get_serial_sequence(:table,:column)'),{'table':name_table,'column':column.name})
                        if sequence:
                            maximum=max((r['id'] for r in rows),default=None)
                            await dest.execute(text('SELECT setval(CAST(:seq AS regclass),:value,:called)'),{'seq':sequence,'value':maximum or 1,'called':maximum is not None})
                revision=await src.scalar(text('SELECT version_num FROM alembic_version'))
                source_constraints=(await src.execute(text(
                    "SELECT c.relname, p.conname, pg_get_constraintdef(p.oid) AS definition "
                    "FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'"
                ))).mappings().all()
            def add_fks(sync):
                for constraint in constraints:sync.execute(AddConstraint(constraint))
            await dest.run_sync(add_fks)
            target_constraints=(await dest.execute(text(
                "SELECT c.relname, p.conname, pg_get_constraintdef(p.oid) AS definition "
                "FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'"
            ))).mappings().all()
            target_names={(r['relname'],r['definition']):r['conname'] for r in target_constraints}
            for constraint in source_constraints:
                old_name=target_names.get((constraint['relname'],constraint['definition']))
                new_name=constraint['conname']
                if old_name is not None and old_name != new_name:
                    await dest.execute(text(f"ALTER TABLE {q(constraint['relname'])} RENAME CONSTRAINT {q(old_name)} TO {q(new_name)}"))
            await dest.execute(text('CREATE TABLE alembic_version (version_num varchar(32) NOT NULL PRIMARY KEY)'))
            await dest.execute(text('INSERT INTO alembic_version VALUES (:revision)'),{'revision':revision})
        print('ISOLATED COPY READY',name,'rows',count,flush=True)
        env=dict(os.environ,DATABASE_URL=url.set(database=name).render_as_string(hide_password=False),RUN_POSTGRES_E2E='1',PYTHONDONTWRITEBYTECODE='1')
        proc=await asyncio.create_subprocess_exec(sys.executable,'-m','pytest',*sys.argv[1:],'-q','-p','no:cacheprovider',env=env)
        result=await proc.wait();print('COPY_TEST_EXIT',result,flush=True)
        return result
    finally:
        await target.dispose();await source.dispose()
        if created:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            print('ISOLATED COPY REMOVED',flush=True)
        await admin.dispose()
if __name__=='__main__':sys.exit(asyncio.run(main()))
