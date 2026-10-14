"""Historical payroll freeze migration contract tests.

Static checks only. PostgreSQL behavior needs isolated E2E.
"""

import ast
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

MIGRATION = (
    ROOT
    / "alembic"
    / "versions"
    / "13d4a7b8c026_payroll_historical_source_freeze.py"
)

TABLES = {
    "payroll_calculations",
    "payroll_calculation_lines",
    "payroll_inputs",
    "payroll_input_salary_slices",
    "employee_salary_rates",
    "payroll_supplements",
    "payroll_vacation_calculations",
    "payroll_sick_leave_calculations",
    "leave_requests",
}


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location(
        "payroll_history_freeze_migration",
        MIGRATION,
    )

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def test_migration_revision_contract(migration):
    assert migration.revision == "13d4a7b8c026"
    assert migration.down_revision == "13d4a7b8c025"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_all_nine_sources_have_explicit_triggers(migration):
    assert set(migration.PROTECTED_TABLES) == TABLES
    assert len(migration.PROTECTED_TABLES) == 9

    names = [
        migration._trigger_name(table)
        for table in migration.PROTECTED_TABLES
    ]

    assert len(names) == len(set(names))

    for name in names:
        assert len(name.encode("utf-8")) <= 63


def test_referenced_source_mutations_rejected(migration):
    sql = migration.CREATE_FUNCTION_SQL

    assert "CREATE FUNCTION" in sql
    assert "RETURNS trigger" in sql
    assert "LANGUAGE plpgsql" in sql
    assert "TG_TABLE_NAME" in sql
    assert "TG_OP" in sql
    assert "protected_row := TRUE" in sql
    assert "IF protected_row THEN" in sql
    assert "ERRCODE = '23514'" in sql

    for table in TABLES:
        assert table in sql


def test_calculation_history_is_append_only(migration):
    sql = migration.CREATE_FUNCTION_SQL

    assert "'payroll_calculations'" in sql
    assert "'payroll_calculation_lines'" in sql
    assert "protected_row := TRUE" in sql


def test_unreferenced_source_update_remains_possible(migration):
    sql = migration.CREATE_FUNCTION_SQL

    assert "protected_row boolean := FALSE" in sql

    assert "IF TG_OP = 'UPDATE' THEN" in sql
    assert "RETURN NEW" in sql
    assert "RETURN OLD" in sql


def test_upgrade_and_downgrade_are_symmetric(migration, monkeypatch):
    statements = []

    monkeypatch.setattr(
        migration.op,
        "execute",
        statements.append,
    )

    migration.upgrade()

    assert len(statements) == 10

    assert statements[0] == migration.CREATE_FUNCTION_SQL

    for table in TABLES:
        assert any(
            "CREATE TRIGGER" in statement
            and f"ON {table}" in statement
            for statement in statements[1:]
        )

    statements.clear()

    migration.downgrade()

    assert len(statements) == 10

    for table in TABLES:
        assert any(
            "DROP TRIGGER" in statement
            and f"ON {table}" in statement
            for statement in statements[:-1]
        )

    assert statements[-1].startswith("DROP FUNCTION")


def test_no_commit_or_rollback_in_migration():
    source = MIGRATION.read_text(encoding="utf-8")

    tree = ast.parse(source)

    operations = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func

            if isinstance(function, ast.Attribute):
                operations.append(function.attr)

    assert "commit" not in operations
    assert "rollback" not in operations


def test_legacy_mutable_sources_remain_outside_freeze(migration):
    # Period finalization, leave approval and supplements
    # before historical usage are legitimate operations.
    sql = migration.CREATE_FUNCTION_SQL

    assert "payroll_periods" not in migration.PROTECTED_TABLES
    assert "protected_row boolean := FALSE" in sql
    assert "BEFORE INSERT" not in sql
