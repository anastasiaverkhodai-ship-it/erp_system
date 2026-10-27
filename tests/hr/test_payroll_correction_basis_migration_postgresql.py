"""Exercise Alembic round trips on an empty disposable database.

A populated clone may contain immutable cash/payment history introduced after
028. It must never be cleared merely to make an older downgrade test pass.
"""
import os
import subprocess
import sys
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings
import pytest


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')
async def test_actual_basis_migration_roundtrip():
    source=make_url(settings.database_url)
    assert (source.database or '').startswith('erp_r3_copy_')
    name='erp_r3_copy_basis_'+uuid4().hex[:12]
    admin=create_async_engine(source.set(database='postgres'),isolation_level='AUTOCOMMIT')
    env=dict(os.environ,DATABASE_URL=source.set(database=name).render_as_string(hide_password=False))
    def cli(*args):
        result=subprocess.run([sys.executable,'-m','alembic',*args],env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stdout+result.stderr
    created=False
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
            created=True
        cli('upgrade','13d4a7b8c028')
        cli('downgrade','13d4a7b8c027')
        cli('upgrade','13d4a7b8c028')
        cli('upgrade','head')
        cli('check')
        cli('downgrade','13d4a7b8c025')
        cli('upgrade','head')
        cli('check')
    finally:
        if created:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
