"""Non-DB persistence contract tests."""

import ast
from pathlib import Path

from app.services.payroll_severance_snapshot_persistence_service import (
    _SNAPSHOT_FIELDS,
)
from app.services.payroll_severance_statutory_snapshot import (
    SeveranceStatutorySnapshot,
)


SERVICE = Path(
    "app/services/payroll_severance_snapshot_persistence_service.py"
)


def read_tree():
    return ast.parse(SERVICE.read_text(encoding="utf-8"))


def function(name):
    matches = [
        node
        for node in read_tree().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def test_all_snapshot_fields_have_mapping():
    assert _SNAPSHOT_FIELDS == tuple(
        SeveranceStatutorySnapshot.__dataclass_fields__
    )


def test_atomic_service_exists():
    assert isinstance(
        function("persist_severance_statutory_snapshot"),
        ast.AsyncFunctionDef,
    )


def test_snapshot_validation_before_write():
    source = ast.unparse(
        function("persist_severance_statutory_snapshot")
    )
    assert "snapshot_to_record_values(snapshot)" in source


def test_parent_row_lock_is_required():
    source = ast.unparse(
        function("persist_severance_statutory_snapshot")
    )
    assert ".with_for_update()" in source


def test_existing_snapshot_must_match():
    source = ast.unparse(
        function("persist_severance_statutory_snapshot")
    )
    assert "persisted != snapshot" in source


def test_no_explicit_commit_or_rollback():
    tree = read_tree()

    forbidden = {"commit", "rollback"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                assert func.attr not in forbidden


def test_no_financial_or_statutory_execution():
    content = SERVICE.read_text(encoding="utf-8")

    assert "calculate_payroll_statutory_result(" not in content
    assert "calculate_payroll_deduction_result(" not in content
    assert "create_payroll_disbursement(" not in content


def test_snapshot_is_reverified_from_record():
    source = ast.unparse(function("snapshot_from_record"))
    assert "verify_severance_statutory_snapshot(snapshot)" in source


def test_insert_uses_transaction_owned_flush():
    source = ast.unparse(
        function("persist_severance_statutory_snapshot")
    )
    assert "await session.flush()" in source
    assert "session.commit()" not in source
