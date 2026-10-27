"""Migration credentials configuration contract."""

import pytest

from app.core.migration_database_url import (
    resolve_migration_database_url,
)

RUNTIME = (
    "postgresql+asyncpg://erp_runtime:runtime_password"
    "@localhost:5432/erp"
)
MIGRATION = (
    "postgresql+asyncpg://erp_migrator:migration_password"
    "@localhost:5432/erp"
)


def test_fallback_when_unconfigured(monkeypatch):
    monkeypatch.delenv("ERP_MIGRATION_DATABASE_URL", raising=False)
    assert resolve_migration_database_url(RUNTIME) == RUNTIME


def test_separate_credentials(monkeypatch):
    monkeypatch.setenv("ERP_MIGRATION_DATABASE_URL", MIGRATION)
    assert resolve_migration_database_url(RUNTIME) == MIGRATION


def test_same_user_rejected(monkeypatch):
    monkeypatch.setenv("ERP_MIGRATION_DATABASE_URL", RUNTIME)
    with pytest.raises(ValueError, match="distinct"):
        resolve_migration_database_url(RUNTIME)


def test_other_database_rejected(monkeypatch):
    monkeypatch.setenv(
        "ERP_MIGRATION_DATABASE_URL",
        MIGRATION.replace("/erp", "/other_database"),
    )
    with pytest.raises(ValueError, match="same database"):
        resolve_migration_database_url(RUNTIME)


def test_other_host_rejected(monkeypatch):
    monkeypatch.setenv(
        "ERP_MIGRATION_DATABASE_URL",
        MIGRATION.replace("@localhost:", "@otherhost:"),
    )
    with pytest.raises(ValueError, match="same database"):
        resolve_migration_database_url(RUNTIME)


def test_other_backend_rejected(monkeypatch):
    monkeypatch.setenv(
        "ERP_MIGRATION_DATABASE_URL",
        "sqlite:///tmp/migration.db",
    )
    with pytest.raises(ValueError, match="backends"):
        resolve_migration_database_url(RUNTIME)
