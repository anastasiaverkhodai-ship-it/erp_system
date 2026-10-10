"""Guarantee snapshots survive migrations; downgrade refuses financial history."""
import importlib.util
import os
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.autogenerate import compare_metadata
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError
from app.core.database import Base
from test_employee_foundation import employee_engine
from test_payroll_minimum_wage_postgresql import inputs
from app.services.payroll_calculation_service import calculate_payroll_input

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def test_minimum_wage_roundtrip_and_history_guard(employee_engine):
    spec=importlib.util.spec_from_file_location('minimum_migration',Path('alembic/versions/13d4a7b8c015_payroll_minimum_wage_assessment.py'))
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    async with employee_engine.begin() as connection:
        def roundtrip(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
                migration.upgrade()
            assert compare_metadata(MigrationContext.configure(sync),Base.metadata)==[]
        await connection.run_sync(roundtrip)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,source=await inputs(db)
        result=await calculate_payroll_input(db,company_id=f['company'],payroll_input_id=source.id,calculated_by=f['actor'])
        saved=dict(result.minimum_wage_assessment)
        def downgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
        with pytest.raises(DBAPIError,match='Cannot downgrade minimum wage'):
            async with db.begin_nested():
                await (await db.connection()).run_sync(downgrade)
        await db.refresh(result)
        assert result.minimum_wage_assessment==saved
        assert await db.scalar(text("SELECT count(*) FROM payroll_calculation_lines WHERE line_type='minimum_wage'"))==1
