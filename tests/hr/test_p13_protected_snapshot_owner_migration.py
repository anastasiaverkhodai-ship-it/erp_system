"""Contract tests for protected ownership migration."""

from pathlib import Path
import ast


MIGRATION = Path(
    "alembic/versions/"
    "p13own261105_protected_regulatory_snapshot_ownership.py"
)


def test_migration_revision_contract():
    tree = ast.parse(MIGRATION.read_text())

    values = {}

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue

        for target in node.targets:
            if isinstance(target, ast.Name):
                if target.id in {"revision", "down_revision"}:
                    values[target.id] = ast.literal_eval(node.value)

    assert values["revision"] == "p13own261105"
    assert values["down_revision"] == "p13regint261105"


def test_immutable_owner_contract():
    source = MIGRATION.read_text()

    assert (
        "ALTER TABLE" in source
        and "OWNER TO erp_snapshot_owner" in source
    )

    assert "ALTER FUNCTION" in source
    assert "protect_regulatory_internal_snapshot()" in source

    assert "GRANT SELECT, INSERT" in source
    assert "REVOKE UPDATE, DELETE, TRUNCATE" in source
    assert "ALTER SEQUENCE" not in source
