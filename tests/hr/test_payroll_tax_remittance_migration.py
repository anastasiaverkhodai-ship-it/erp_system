import importlib.util
import os
from pathlib import Path
import pytest
from sqlalchemy import select,func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.autogenerate import compare_metadata
from app.core.database import Base
from app.models.payroll_tax_remittance import PayrollTaxRemittance
from test_employee_foundation import employee_engine
from test_payroll_tax_remittance import setup,payment,request
from app.services.payroll_tax_remittance_service import register_tax_remittance
from decimal import Decimal as D
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def test_funding_migration_roundtrip_and_history_guard(employee_engine):
    spec=importlib.util.spec_from_file_location('funding_migration',Path('alembic/versions/13d4a7b8c020_payroll_tax_remittance.py'))
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    async with employee_engine.begin() as connection:
        def roundtrip(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade();migration.upgrade()
            assert compare_metadata(MigrationContext.configure(sync),Base.metadata)==[]
        await connection.run_sync(roundtrip)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        journal=await payment(db,company,user,accounts,bank,D(400))
        await register_tax_remittance(db,company_id=company.id,data=request(journal,bank,calc,D(400),'guard'),created_by=user.id)
        def downgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
        with pytest.raises(DBAPIError,match='Cannot discard'):
            async with db.begin_nested():
                await (await db.connection()).run_sync(downgrade)
        assert await db.scalar(select(func.count(PayrollTaxRemittance.id)))==1
