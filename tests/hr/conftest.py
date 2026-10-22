"""Separate databases for destructive migration roundtrip tests."""
import asyncio
import os
import re
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings


@pytest.fixture
def empty_migration_database(monkeypatch):
    if os.getenv('RUN_POSTGRES_E2E') != '1':
        pytest.skip('Isolated PostgreSQL required')
    parent = make_url(str(settings.database_url))
    assert re.fullmatch(r'erp_r3_copy_[0-9a-f]{16}', parent.database or '')
    assert parent.host in ('localhost', '127.0.0.1', '::1')
    name = 'erp_r3_copy_' + uuid4().hex[:16]
    target = parent.set(database=name).render_as_string(hide_password=False)

    async def database_command(command):
        engine = create_async_engine(parent, isolation_level='AUTOCOMMIT')
        try:
            async with engine.connect() as connection:
                await connection.execute(text(command))
        finally:
            await engine.dispose()

    asyncio.run(database_command(f'CREATE DATABASE "{name}"'))
    try:
        monkeypatch.setenv('DATABASE_URL', target)
        monkeypatch.setattr(settings, 'database_url', target)
        result = subprocess.run(
            [sys.executable, '-m', 'alembic', 'upgrade', 'head'],
            capture_output=True, text=True, timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        yield
    finally:
        asyncio.run(database_command(f'DROP DATABASE "{name}"'))
