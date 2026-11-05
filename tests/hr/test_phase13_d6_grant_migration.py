"""Contract tests for the D6 runtime grants."""

import ast
from pathlib import Path


MIGRATION = Path(
    "alembic/versions/"
    "p13d6grant261105_runtime_read_permissions.py"
)


def test_revision_chain():
    tree = ast.parse(MIGRATION.read_text())

    assignments = {}

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue

        for target in node.targets:
            if isinstance(target, ast.Name):
                if target.id in (
                    "revision", "down_revision"
                ):
                    assignments[target.id] = ast.literal_eval(
                        node.value
                    )

    assert assignments == {
        "revision": "p13d6grant261105",
        "down_revision": "p13own261105",
    }


def test_minimal_grants():
    source = MIGRATION.read_text()

    assert "GRANT SELECT" in source
    assert "public.payroll_d6_evidence," in source
    assert "public.payroll_d6_evidence_reviews" in source
    assert "TO erp_user" in source

    for command in (
        "GRANT ALL",
        "GRANT UPDATE",
        "GRANT DELETE",
        "ALTER TABLE",
        "ALTER FUNCTION",
    ):
        assert command not in source
