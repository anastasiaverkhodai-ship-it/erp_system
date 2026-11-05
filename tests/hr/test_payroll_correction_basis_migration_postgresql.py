"""Exercise Alembic round trips on an empty disposable database.

A populated clone may contain immutable cash/payment history introduced after
028. It must never be cleared merely to make an older downgrade test pass.
"""
import os
import subprocess
import sys
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings
import pytest


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')
async def test_actual_basis_migration_roundtrip():
    source=make_url(settings.database_url)
    assert (source.database or '').startswith('erp_r3_copy_')
    name='erp_r3_copy_basis_'+uuid4().hex[:12]
    # This is a nested migration test, not an application operation.
    # The outer PostgreSQL clone runs application checks as erp_user.
    # Database lifecycle and Alembic must use the maintenance role.
    assert source.username == 'erp_user'
    assert os.environ.get('USER') == 'anastasia'
    admin_url = URL.create(
        'postgresql+asyncpg',
        username='anastasia',
        database='postgres',
    )
    admin=create_async_engine(
        admin_url,
        isolation_level='AUTOCOMMIT',
    )
    env=dict(
        os.environ,
        DATABASE_URL=admin_url.set(
            database=name,
        ).render_as_string(hide_password=False),
    )
    # Prevent inherited migration overrides from redirecting Alembic.
    env.pop('ERP_MIGRATION_DATABASE_URL', None)
    env.pop('ERP_MIGRATION_OWNER_ROLE', None)
    def cli(*args):
        result=subprocess.run([sys.executable,'-m','alembic',*args],env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stdout+result.stderr
    created=False
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}" OWNER erp_user TEMPLATE template0'))
            created=True
        # Exercise historical migrations before reaching the
        # deliberately non-reversible D6 permission hardening.
        cli('upgrade','13d4a7b8c028')
        cli('downgrade','13d4a7b8c027')
        cli('upgrade','13d4a7b8c028')
        cli('downgrade','13d4a7b8c025')
        cli('upgrade','13d4a7b8c028')

        # From here forward, the protected migration chain
        # must remain intact.
        cli('upgrade','head')
        cli('check')

        # Negative control: the current D6 permission hardening
        # must refuse an unreviewed historical downgrade.
        rejected = subprocess.run(
            [
                sys.executable,
                '-m',
                'alembic',
                'downgrade',
                '13d4a7b8c025',
            ],
            env=env,
            capture_output=True,
            text=True,
        )

        assert rejected.returncode != 0
        assert (
            'D6 permission hardening must be reviewed before downgrade'
            in rejected.stdout + rejected.stderr
        )

        # The failed downgrade must not advance or roll back
        # the database revision.
        current = subprocess.run(
            [sys.executable, '-m', 'alembic', 'current'],
            env=env,
            capture_output=True,
            text=True,
        )

        assert current.returncode == 0, (
            current.stdout + current.stderr
        )
        assert 'p13d6grant261105' in (
            current.stdout + current.stderr
        )

        cli('check')
        print('HISTORICAL MIGRATION ROUNDTRIP = PASS')
        print('PROTECTED D6 DOWNGRADE REJECTION = PASS')
        print('FAILED DOWNGRADE REVISION PRESERVATION = PASS')

    finally:
        if created:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
