"""Contracts for isolated PostgreSQL regression roles."""

import ast
from pathlib import Path


RUNNER = Path(
    "scripts/run_postgresql_regression_copy.py"
)


def test_dump_and_restore_preserve_security():
    source = RUNNER.read_text(encoding="utf-8")
    assert '"--no-owner"' not in source
    assert '"--no-acl"' not in source
    assert 'admin_url.set(database="erp_db")' in source
    assert "admin_url.set(database=database_name)" in source


def test_separate_maintenance_and_runtime():
    source = RUNNER.read_text(encoding="utf-8")
    ast.parse(source)

    assert "migration_env = dict(test_env)" in source
    assert "env=migration_env" in source
    assert "env=test_env" in source
    assert 'test_env["DATABASE_URL"]' in source
    assert 'test_env["RUN_POSTGRES_E2E"]' in source


def test_clone_safety_guards_remain():
    source = RUNNER.read_text(encoding="utf-8")

    assert "NAME_PATTERN.fullmatch(database_name)" in source
    assert "remove_isolated_database" in source
    assert "SOURCE ALEMBIC REVISION UNCHANGED" in source
