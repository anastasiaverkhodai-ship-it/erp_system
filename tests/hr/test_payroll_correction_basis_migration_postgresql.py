"""Exercise the actual Alembic CLI on the disposable clone only."""
import os
import subprocess
import sys
from sqlalchemy.engine import make_url
from app.core.config import settings
import pytest


@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')
def test_actual_basis_migration_roundtrip():
    assert (make_url(settings.database_url).database or '').startswith('erp_r3_copy_')
    def cli(*args):
        result = subprocess.run([sys.executable, '-m', 'alembic', *args], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    cli('upgrade', '13d4a7b8c028')
    try:
        cli('downgrade', '13d4a7b8c027')
        cli('upgrade', '13d4a7b8c028')
        cli('check')
    finally:
        cli('downgrade', '13d4a7b8c025')
