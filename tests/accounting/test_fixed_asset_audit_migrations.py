"""Exercise audit migration upgrades/downgrades in a private PostgreSQL schema."""
import importlib.util
import os
from pathlib import Path
import pytest
from sqlalchemy import text, insert
from app.models.company import Company
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_fixed_asset_commissioning import engine

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')]

async def test_audit_migrations_roundtrip_and_preserve_existing_data(engine):
    migrations=[]
    for path in sorted(Path('alembic/versions').glob('8fa12510c00*.py')):
        spec=importlib.util.spec_from_file_location(path.stem,path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        migrations.append(module)
    assert len(migrations)==4
    async with engine.begin() as conn:
        await conn.execute(insert(Company).values(name="Migration preservation probe"))
        before=(await conn.execute(text('SELECT to_jsonb(t) FROM companies t'))).scalars().all()
        def exercise(sync):
            with Operations.context(MigrationContext.configure(sync)):
                for migration in reversed(migrations): migration.downgrade()
                for migration in migrations: migration.upgrade()
        await conn.run_sync(exercise)
        assert (await conn.execute(text('SELECT to_jsonb(t) FROM companies t'))).scalars().all()==before
        columns=(await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='fixed_asset_depreciations'"))).scalars().all()
        assert {'actual_output','expected_output'} <= set(columns)
