"""Safety tests for migration owner role activation."""

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from app.core.migration_role import apply_migration_owner_role


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class Connection:
    dialect = postgresql.dialect()

    def __init__(self, active="erp_schema_owner"):
        self.active = active
        self.calls = []

    def exec_driver_sql(self, statement):
        self.calls.append(statement)
        return Result(self.active)

    def commit(self):
        self.calls.append("COMMIT")

    def in_transaction(self):
        return False


def configure(monkeypatch, role="erp_schema_owner"):
    monkeypatch.setenv(
        "ERP_MIGRATION_DATABASE_URL",
        "postgresql+asyncpg://erp_migrator@localhost/erp",
    )
    monkeypatch.setenv("ERP_MIGRATION_OWNER_ROLE", role)


def test_no_owner_override(monkeypatch):
    monkeypatch.delenv("ERP_MIGRATION_OWNER_ROLE", raising=False)
    db = Connection()
    assert apply_migration_owner_role(db) is False
    assert db.calls == []


def test_activation(monkeypatch):
    configure(monkeypatch)
    db = Connection()
    assert apply_migration_owner_role(db) is True
    assert db.calls == [
        "SET ROLE erp_schema_owner",
        "SELECT current_user",
        "COMMIT",
    ]


def test_requires_separate_migration_url(monkeypatch):
    monkeypatch.delenv(
        "ERP_MIGRATION_DATABASE_URL", raising=False
    )
    monkeypatch.setenv(
        "ERP_MIGRATION_OWNER_ROLE", "erp_schema_owner"
    )
    with pytest.raises(RuntimeError, match="separate"):
        apply_migration_owner_role(Connection())


def test_invalid_identifier(monkeypatch):
    configure(monkeypatch, "owner; DROP TABLE accounting")
    with pytest.raises(ValueError, match="identifier"):
        apply_migration_owner_role(Connection())


def test_reject_wrong_active_role(monkeypatch):
    configure(monkeypatch)
    with pytest.raises(RuntimeError, match="not activated"):
        apply_migration_owner_role(
            Connection(active="erp_migrator")
        )


def test_reject_non_postgresql(monkeypatch):
    configure(monkeypatch)
    db = Connection()
    db.dialect = sqlite.dialect()
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        apply_migration_owner_role(db)


def test_role_activation_clears_implicit_transaction(monkeypatch):
    configure(monkeypatch)
    db = Connection()
    assert apply_migration_owner_role(db) is True
    assert db.calls[-1] == "COMMIT"
    assert db.in_transaction() is False


def test_failed_role_activation_does_not_commit(monkeypatch):
    configure(monkeypatch)
    db = Connection(active="erp_migrator")
    with pytest.raises(RuntimeError, match="not activated"):
        apply_migration_owner_role(db)
    assert "COMMIT" not in db.calls
