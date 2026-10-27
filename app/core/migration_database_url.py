"""Separate migration URL from ERP application runtime URL."""

import os

from sqlalchemy.engine import make_url


def resolve_migration_database_url(runtime_url: str) -> str:
    override = os.environ.get("ERP_MIGRATION_DATABASE_URL")

    if not override:
        return runtime_url

    runtime = make_url(runtime_url)
    migration = make_url(override)

    if runtime.get_backend_name() != migration.get_backend_name():
        raise ValueError("Database backends must match")

    if (
        runtime.host != migration.host
        or runtime.port != migration.port
        or runtime.database != migration.database
    ):
        raise ValueError("Migration URL must target the same database")

    if not runtime.username or not migration.username:
        raise ValueError("Database users must be explicit")

    if runtime.username == migration.username:
        raise ValueError("Migration and runtime users must be distinct")

    return override
