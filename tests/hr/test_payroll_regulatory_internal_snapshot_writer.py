import ast
from pathlib import Path

from app.services.payroll_regulatory_internal_snapshot_writer import (
    InternalRegulatorySnapshotWriteError,
    _positive,
)


def test_invalid_identifiers():
    for invalid in (0, -1, True, "1", None):
        try:
            _positive(invalid, "id")
        except InternalRegulatorySnapshotWriteError:
            continue
        raise AssertionError("Invalid identifier accepted")


def test_no_transaction_ownership_escalation():
    source = Path(
        "app/services/payroll_regulatory_internal_snapshot_writer.py"
    ).read_text()

    tree = ast.parse(source)

    method_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("commit", "rollback")
    ]

    assert method_calls == []


def test_no_official_generation_writer():
    source = Path(
        "app/services/payroll_regulatory_internal_snapshot_writer.py"
    ).read_text()

    assert "generate_payroll_regulatory_report(" not in source
    assert "status = 'generated'" not in source


def test_migration_is_append_only():
    source = Path(
        "alembic/versions/p13regint261105_internal_regulatory_snapshots.py"
    ).read_text()

    assert "BEFORE UPDATE OR DELETE" in source
    assert "BEFORE TRUNCATE" in source
    assert "internal_review_only" in source
    assert "p13d6ev261104" in source
