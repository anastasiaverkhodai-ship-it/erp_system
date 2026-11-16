"""Fail-closed bank batch gate: read-only source fingerprint, disposable copy, tests.

Run with PYTHONPATH=. .venv/bin/python -m scripts.run_phase13_bank_batch_gate.
No production migrations, commits or pushes are performed by this command.
Only allowlisted task paths may differ; other pre-existing files are hashed.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SCOPE = (
    "app/models/__init__.py", "app/models/bank_statement_reconciliation.py",
    "app/models/payroll_bank_batch.py", "app/services/payroll_bank_batch_service.py",
    "app/services/payroll_bank_batch_validation.py", "app/services/accounting_reversal.py",
    "app/services/bank_statement_reconciliation_service.py",
    "app/services/payroll_bank_reconciliation_service.py",
    "alembic/versions/p13bankbatch261116_payroll_bank_batch.py",
    "tests/hr/test_payroll_bank_batch_postgresql.py",
    "tests/hr/test_payroll_bank_batch_migration_postgresql.py",
    "tests/hr/test_phase13_cross_calculation_real_postgresql.py",
    "scripts/run_phase13_bank_batch_gate.py", "docs/phase13_bank_batch_acceptance.md",
)
TESTS = (
    "tests/hr/test_payroll_bank_batch_migration_postgresql.py",
    "tests/hr/test_payroll_bank_batch_postgresql.py",
    "tests/hr/test_phase13_cross_calculation_real_postgresql.py",
    "tests/hr/test_phase13_cross_calculation_atomic_postgresql.py",
    "tests/hr/test_payroll_disbursement_postgresql.py",
    "tests/hr/test_payroll_cross_calculation_posting.py",
    "tests/test_bank_statement_reconciliation_service.py",
    "tests/test_bank_statement_reconciliation_foundation.py",
)


def protected_files():
    paths = subprocess.check_output(["git", "ls-files", "--modified", "--others", "--exclude-standard", "-z"]).decode().split("\0")
    return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in paths if p and p not in SCOPE and Path(p).is_file()}


def source_fingerprint():
    from sqlalchemy.engine import URL, make_url
    from app.core.config import settings
    from scripts.run_postgresql_regression_copy import pg_environment
    source = make_url(settings.database_url)
    assert source.database == "erp_db" and source.host in ("localhost", "127.0.0.1", "::1")
    assert os.environ.get("USER") == "anastasia"
    env = pg_environment(URL.create("postgresql", username="anastasia", database="erp_db"))
    # The dump exists only in private memory; output contains only its digest.
    result = subprocess.run(["pg_dump", "--no-password", "--format=plain", "erp_db"],
                            env=env, capture_output=True, check=True)
    stable = b"\n".join(line for line in result.stdout.splitlines()
        if not line.startswith((b"\\restrict ", b"\\unrestrict ")))
    return hashlib.sha256(stable).hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    evidence = Path(tempfile.mkdtemp(prefix="erp_phase13_bank_gate_"))
    baseline = protected_files()
    source_before = source_fingerprint()
    (evidence / "protected.json").write_text(json.dumps(baseline, indent=2))
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)}
    with (evidence / "tests.log").open("w") as log:
        result = subprocess.run([sys.executable, "-m", "scripts.run_postgresql_regression_copy",
            "--upgrade-copy", *TESTS, "--junitxml=" + str(evidence / "tests.xml")],
            env=env, stdout=log, stderr=subprocess.STDOUT)
    source_after = source_fingerprint()
    preserved = baseline == protected_files()
    summary = dict(test_exit=result.returncode, protected_files=len(baseline),
        unrelated_worktree_preserved=preserved, production_dump_unchanged=source_before == source_after,
        production_sha256_before=source_before, production_sha256_after=source_after,
        official_generation="BLOCKED", phase13="OPEN", evidence=str(evidence))
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    assert result.returncode == 0, "STOP: tests or copy migration failed"
    assert preserved, "STOP: unrelated worktree changed"
    assert source_before == source_after, "STOP: production dump changed"
    print("BANK BATCH ORCHESTRATION GATE = PASS")


if __name__ == "__main__":
    main()
