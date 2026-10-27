"""Safely activate the configured owner role for Alembic."""

import os
import re


def apply_migration_owner_role(connection):
    role = os.environ.get("ERP_MIGRATION_OWNER_ROLE")

    if not role:
        return False

    if not os.environ.get("ERP_MIGRATION_DATABASE_URL"):
        raise RuntimeError(
            "Migration owner role requires separate migration URL"
        )

    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("Invalid migration owner role identifier")

    if connection.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL migration connection required")

    quoted = connection.dialect.identifier_preparer.quote(role)
    connection.exec_driver_sql("SET ROLE " + quoted)

    active = connection.exec_driver_sql(
        "SELECT current_user"
    ).scalar_one()

    if active != role:
        raise RuntimeError("Migration owner role not activated")

    # SET ROLE begins an implicit SQLAlchemy transaction.
    # Commit that transaction before Alembic opens its own
    # migration transaction. Session-level SET ROLE survives COMMIT.
    connection.commit()

    if connection.in_transaction():
        raise RuntimeError(
            "Migration connection still has an active transaction"
        )

    return True
