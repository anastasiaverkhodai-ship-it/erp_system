"""Apply real 026/027 operations to legacy rows in a private schema."""
import importlib.util
import os
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_insert_freeze_postgresql import seed, SLICE_INSERT, LINE_INSERT
from app.services.payroll_calculation_service import calculate_payroll_input
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Isolated PostgreSQL')]


async def apply(engine,revision,direction):
    path=next(Path('alembic/versions').glob(f'13d4a7b8c{revision}_*.py'))
    spec=importlib.util.spec_from_file_location('legacy_seal_'+revision,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    async with engine.begin() as conn:
        def run(sync):
            module.op=Operations(MigrationContext.configure(sync))
            getattr(module,direction)()
        await conn.run_sync(run)


async def test_existing_calculation_is_sealed_on_real_migration(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        args,slice_id=await seed(db)
        calculation=await calculate_payroll_input(db,**args)
        cid,input_id,gross=calculation.id,calculation.payroll_input_id,calculation.gross_amount
        await db.commit()
    await apply(employee_engine,'026','upgrade')
    await apply(employee_engine,'027','upgrade')
    async with AsyncSession(employee_engine) as db:
        assert await db.scalar(text('SELECT history_creation_xid IS NULL FROM payroll_calculations WHERE id=:id'),{'id':cid})
        assert await db.scalar(text('SELECT history_sealed FROM payroll_inputs WHERE id=:id'),{'id':input_id}) is True
        for sql,key,pattern in ((SLICE_INSERT,slice_id,'membership is immutable'),(LINE_INSERT,cid,'lines are sealed')):
            with pytest.raises(DBAPIError,match=pattern):
                async with db.begin_nested():
                    await db.execute(text(sql),{'id':key})
        assert await db.scalar(text('SELECT gross_amount FROM payroll_calculations WHERE id=:id'),{'id':cid})==gross
    await apply(employee_engine,'027','downgrade')
    await apply(employee_engine,'027','upgrade')
    async with employee_engine.connect() as conn:
        assert await conn.scalar(text('SELECT history_sealed FROM payroll_inputs WHERE id=:id'),{'id':input_id}) is True
        assert await conn.scalar(text('SELECT gross_amount FROM payroll_calculations WHERE id=:id'),{'id':cid})==gross
