"""Separate databases for destructive migration roundtrip tests."""
import asyncio
import os
import re
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings


@pytest.fixture
def empty_migration_database(monkeypatch):
    if os.getenv('RUN_POSTGRES_E2E') != '1':
        pytest.skip('Isolated PostgreSQL required')

    from sqlalchemy.engine import URL

    parent = make_url(str(settings.database_url))

    assert re.fullmatch(
        r'erp_r3_copy_[0-9a-f]{16}',
        parent.database or '',
    )
    assert parent.host in ('localhost', '127.0.0.1', '::1')
    assert parent.username == 'erp_user'
    assert os.environ.get('USER') == 'anastasia'

    name = 'erp_r3_copy_' + uuid4().hex[:16]

    # The test itself must continue using the limited app role.
    application_target = parent.set(
        database=name,
    ).render_as_string(hide_password=False)

    # Only the disposable database lifecycle and Alembic
    # migration use the privileged maintenance connection.
    # Preserve the original local PostgreSQL host, port and
    # connection options. Change only role and database.
    maintenance = parent.set(
        username='anastasia',
        password=None,
        database='postgres',
    )

    migration_target = maintenance.set(
        database=name,
    ).render_as_string(hide_password=False)

    async def database_command(command):
        engine = create_async_engine(
            maintenance,
            isolation_level='AUTOCOMMIT',
        )
        try:
            async with engine.connect() as connection:
                await connection.execute(text(command))
        finally:
            await engine.dispose()

    created = False

    try:
        asyncio.run(database_command(
            f'CREATE DATABASE "{name}" OWNER erp_user TEMPLATE template0'
        ))
        created = True

        monkeypatch.setenv('DATABASE_URL', application_target)
        monkeypatch.setattr(
            settings,
            'database_url',
            application_target,
        )

        # Do not expose maintenance credentials to the app.
        # The subprocess-only environment is used for Alembic.
        migration_env = dict(
            os.environ,
            DATABASE_URL=migration_target,
        )
        migration_env.pop(
            'ERP_MIGRATION_DATABASE_URL', None
        )
        migration_env.pop(
            'ERP_MIGRATION_OWNER_ROLE', None
        )

        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'alembic',
                'upgrade',
                '13d4a7b8c063',
            ],
            env=migration_env,
            capture_output=True,
            text=True,
            timeout=180,
        )

        assert result.returncode == 0, (
            result.stdout + result.stderr
        )

        # Nested Alembic subprocesses must use the same
        # maintenance connection that initialized this DB.
        # The application DATABASE_URL remains erp_user.
        # Reproduce the verified application privileges on this
        # disposable database only. Never alter production ACLs.
        async def grant_test_company_access():
            engine = create_async_engine(
                maintenance.set(database=name),
            )
            try:
                async with engine.begin() as connection:
                    await connection.execute(text(
                        'GRANT SELECT, INSERT ON TABLE '
                        'public.companies TO erp_user'
                    ))

                    # PostgreSQL SERIAL/identity access is separate
                    # from table INSERT permissions.
                    sequence = await connection.scalar(text(
                        "SELECT pg_get_serial_sequence("
                        "'public.companies', 'id')"
                    ))
                    if sequence:
                        from sqlalchemy.sql.elements import quoted_name
                        preparer = connection.dialect.identifier_preparer
                        sequence_parts = sequence.split('.')
                        if len(sequence_parts) != 2:
                            raise AssertionError(
                                'Unexpected companies sequence name'
                            )
                        if not all(
                            part.replace('_', '').isalnum()
                            for part in sequence_parts
                        ):
                            raise AssertionError(
                                'Unsafe sequence identifier'
                            )
                        qualified = '.'.join(
                            preparer.quote(part)
                            for part in sequence_parts
                        )
                        await connection.execute(text(
                            'GRANT USAGE, SELECT ON SEQUENCE '
                            + qualified + ' TO erp_user'
                        ))
            finally:
                await engine.dispose()

        asyncio.run(grant_test_company_access())

        monkeypatch.setenv(
            'ERP_MIGRATION_DATABASE_URL',
            migration_target,
        )
        monkeypatch.delenv(
            'ERP_MIGRATION_OWNER_ROLE',
            raising=False,
        )

        assert (
            make_url(os.environ['DATABASE_URL']).username
            == 'erp_user'
        )
        assert (
            make_url(
                os.environ['ERP_MIGRATION_DATABASE_URL']
            ).username
            == 'anastasia'
        )

        yield

    finally:
        if created:
            # Cleanup only the generated, validated test DB.
            assert re.fullmatch(
                r'erp_r3_copy_[0-9a-f]{16}',
                name,
            )
            asyncio.run(database_command(
                f'DROP DATABASE "{name}" WITH (FORCE)'
            ))
