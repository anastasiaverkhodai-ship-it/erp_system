"""Round-trip the new migration on the guarded disposable copy only."""
import os
import re
import subprocess
import sys
from sqlalchemy.engine import make_url, URL


def test_bank_batch_migration_round_trip():
    url = make_url(os.environ["DATABASE_URL"])
    assert url.host in ("localhost", "127.0.0.1")
    assert re.fullmatch(r"erp_r3_copy_[0-9a-f]{16}", url.database or "")
    env = dict(os.environ)
    env["DATABASE_URL"] = URL.create("postgresql+asyncpg", username="anastasia", database=url.database).render_as_string(hide_password=False)
    env.pop("ERP_MIGRATION_DATABASE_URL", None)
    env.pop("ERP_MIGRATION_OWNER_ROLE", None)
    for operation in (("downgrade", "p13paidacl261116"), ("upgrade", "head"), ("check",)):
        result = subprocess.run([sys.executable, "-m", "alembic", *operation], env=env,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-4000:]
