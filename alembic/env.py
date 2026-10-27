import asyncio

from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.core.database import Base
import app.models
from app.core.config import settings
from app.core.migration_database_url import resolve_migration_database_url
from app.core.migration_role import apply_migration_owner_role

config = context.config
config.set_main_option(
    "sqlalchemy.url",
    resolve_migration_database_url(settings.database_url).replace("%", "%%"),
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


from app.models.payroll_advance_tax_remittance import PayrollAdvanceTaxRemittance
from app.models.payroll_d5_professional_evidence import PayrollD5ProfessionalEvidence
from app.models.payroll_sick_correction import PayrollSickCorrection
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # Trigger-owned concurrency state from migration 027, intentionally absent
    # from ORM reads/writes. Never propose deleting these internal columns.
    managed = {
        ('payroll_inputs', 'history_sealed'),
        ('payroll_inputs', 'history_epoch'),
        ('payroll_calculations', 'history_creation_xid'),
    }
    if type_ == 'column' and reflected and compare_to is None:
        return (obj.table.name, name) not in managed
    return True


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection):
    apply_migration_owner_role(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(
            do_run_migrations
        )

    await connectable.dispose()


def run_migrations_online():
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()