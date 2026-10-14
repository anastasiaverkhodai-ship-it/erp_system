"""Current freeze boundary and known limitations.

These tests intentionally document gaps before the next
migration repair. They are not authorization for revision 2.
"""

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

MIGRATION = (
    ROOT
    / "alembic/versions/"
    / "13d4a7b8c026_payroll_historical_source_freeze.py"
)


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location(
        "freeze_gap_contract_migration",
        MIGRATION,
    )

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def test_freeze_applies_to_nine_sources(migration):
    assert len(migration.PROTECTED_TABLES) == 9


def test_existing_function_uses_update_delete_only(migration):
    sql = migration.CREATE_FUNCTION_SQL

    assert "UPDATE" in sql
    assert "DELETE" in sql
    assert "INSERT" not in sql


def test_calculation_history_is_update_delete_immutable(migration):
    assert "payroll_calculations" in migration.PROTECTED_TABLES
    assert "payroll_calculation_lines" in migration.PROTECTED_TABLES


def test_salary_history_is_update_delete_immutable(migration):
    assert "employee_salary_rates" in migration.PROTECTED_TABLES
    assert "payroll_input_salary_slices" in migration.PROTECTED_TABLES


def test_no_historical_insert_freeze_yet(migration):
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "BEFORE UPDATE OR DELETE" in sql
    assert "BEFORE INSERT OR UPDATE OR DELETE" not in sql


def test_revision_is_still_fail_closed():
    from app.services.payroll_correction_recalculation_service import (
        recalculate_minimum_wage_correction,
    )

    import inspect

    source = inspect.getsource(
        recalculate_minimum_wage_correction
    )

    assert "source_evidence_complete" in source
    assert "correction revision creation is forbidden" in source


def test_historical_evidence_is_not_declared_complete():
    from app.services.payroll_correction_source_evidence_service import (
        capture_payroll_correction_source,
    )

    import inspect

    source = inspect.getsource(
        capture_payroll_correction_source
    )

    assert '"source_evidence_complete": False' in source
    assert '"automatic_revision_creation_allowed": False' in source
