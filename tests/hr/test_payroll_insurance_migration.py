import importlib.util
import os
from pathlib import Path
import pytest
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import AsyncSession
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.autogenerate import compare_metadata
from app.core.database import Base
from app.models.payroll_insurance_evidence import PayrollInsuranceEvidence
from test_employee_foundation import employee_engine
from test_payroll_insurance_evidence import insurance_setup
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def test_insurance_schema_roundtrip_and_history_guard(employee_engine):
    spec=importlib.util.spec_from_file_location('insurance_migration',Path('alembic/versions/13d4a7b8c016_payroll_insurance_evidence.py'))
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    replacement_spec=importlib.util.spec_from_file_location('replacement_migration',Path('alembic/versions/13d4a7b8c019_first_day_sick_salary.py'))
    replacement=importlib.util.module_from_spec(replacement_spec);replacement_spec.loader.exec_module(replacement)
    async with employee_engine.begin() as connection:
        def roundtrip(sync):
            with Operations.context(MigrationContext.configure(sync)):
                replacement.downgrade();migration.downgrade();migration.upgrade();replacement.upgrade()
            assert compare_metadata(MigrationContext.configure(sync),Base.metadata)==[]
        await connection.run_sync(roundtrip)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        await insurance_setup(db)
        def downgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
        with pytest.raises(RuntimeError,match='historical records'):
            await (await db.connection()).run_sync(downgrade)
        assert await db.scalar(select(func.count(PayrollInsuranceEvidence.id)))==1
