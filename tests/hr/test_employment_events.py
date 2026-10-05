import os
from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_employee_foundation import employee_engine
from test_employment_structure import setup, contract
from app.models.employment_event import EmploymentEvent
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollInput, PayrollPeriod
from app.schemas.employment_event import EmploymentEventCreate, EmploymentEventReverse, EmploymentEventBatch
from app.services.employment_event_service import create_employment_event, reverse_employment_event, employment_state_on, create_employment_event_batch
from app.services.employment_structure_service import update_employment_contract, EmploymentStructureLifecycleError, EmploymentStructureNotFoundError

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


def event(kind='transfer', day=date(2026,2,1), key='change', **values):
    return EmploymentEventCreate(event_type=kind,effective_date=day,order_number='ORDER-'+key,
        order_date=day,reason='Documented personnel decision',request_key=key,**values)


async def test_history_retry_reversal_and_direct_edit_guard(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db);c=await contract(db,f)
        hire=await create_employment_event(db,company_id=f['company'],contract_id=c.id,
            data=event('hire',date(2026,1,10),'hire'),created_by=f['actor'])
        data=event(work_arrangement='part_time')
        transfer=await create_employment_event(db,company_id=f['company'],contract_id=c.id,data=data,created_by=f['actor'])
        assert (await create_employment_event(db,company_id=f['company'],contract_id=c.id,data=data,created_by=f['actor'])).id==transfer.id
        with pytest.raises(EmploymentStructureLifecycleError,match='different'):
            await create_employment_event(db,company_id=f['company'],contract_id=c.id,
                data=event(position_id=None),created_by=f['actor'])
        old=await employment_state_on(db,company_id=f['company'],contract_id=c.id,on_date=date(2026,1,31))
        new=await employment_state_on(db,company_id=f['company'],contract_id=c.id,on_date=date(2026,2,1))
        assert old['terms']['work_arrangement']=='full_time'
        assert new['terms']['work_arrangement']=='part_time'
        assert old['history_complete']
        with pytest.raises(EmploymentStructureLifecycleError,match='documented'):
            await update_employment_contract(db,company_id=f['company'],contract_id=c.id,
                work_arrangement='full_time',fields_set={'work_arrangement'},changed_by=f['actor'])
        reverse=EmploymentEventReverse(order_number='UNDO',order_date=date(2026,2,2),reason='Incorrect order',request_key='undo')
        reversed_event=await reverse_employment_event(db,company_id=f['company'],contract_id=c.id,
            event_id=transfer.id,data=reverse,created_by=f['actor'])
        assert reversed_event.reversal_of_id==transfer.id
        assert (await employment_state_on(db,company_id=f['company'],contract_id=c.id,on_date=date(2026,2,1)))['terms']['work_arrangement']=='full_time'
        assert (await reverse_employment_event(db,company_id=f['company'],contract_id=c.id,
            event_id=transfer.id,data=reverse,created_by=f['actor'])).id==reversed_event.id
        with pytest.raises(EmploymentStructureNotFoundError):
            await employment_state_on(db,company_id=f['company']+100000,contract_id=c.id,on_date=date(2026,2,1))


async def test_atomic_primary_to_secondary_transition(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db);primary=await contract(db,f)
        secondary=await contract(db,f,number='SECOND',employment_kind='internal_secondary')
        ending=event('termination',date(2026,1,31),'end-primary')
        promotion=event(day=date(2026,2,1),key='promote',employment_kind='primary')
        with pytest.raises(EmploymentStructureLifecycleError,match='covering'):
            await create_employment_event(db,company_id=f['company'],contract_id=primary.id,
                data=ending,created_by=f['actor'])
        assert await db.scalar(select(func.count(EmploymentEvent.id)))==0
        await db.refresh(primary)
        batch=EmploymentEventBatch(events=[{'contract_id':primary.id,'event':ending},
                                          {'contract_id':secondary.id,'event':promotion}])
        rows=await create_employment_event_batch(db,company_id=f['company'],data=batch,created_by=f['actor'])
        assert len(rows)==2
        before=await employment_state_on(db,company_id=f['company'],contract_id=secondary.id,on_date=date(2026,1,31))
        after=await employment_state_on(db,company_id=f['company'],contract_id=secondary.id,on_date=date(2026,2,1))
        assert before['terms']['employment_kind']=='internal_secondary'
        assert after['terms']['employment_kind']=='primary'
        assert (await db.get(EmploymentContract,primary.id)).end_date==date(2026,1,31)


async def test_finalized_period_protects_events(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db);c=await contract(db,f)
        period=PayrollPeriod(company_id=f['company'],year=2026,month=2,start_date=date(2026,2,1),
            end_date=date(2026,2,28),status='finalized',created_by=f['actor'],finalized_by=f['actor'],
            finalized_at=datetime.now(timezone.utc))
        db.add(period);await db.flush()
        db.add(PayrollInput(company_id=f['company'],payroll_period_id=period.id,employment_contract_id=c.id,
            scheduled_minutes=0,worked_minutes=0,leave_days=0,sick_days=0,manual_adjustment_amount=Decimal(0),created_by=f['actor']))
        await db.flush()
        with pytest.raises(EmploymentStructureLifecycleError,match='finalized'):
            await create_employment_event(db,company_id=f['company'],contract_id=c.id,
                data=event(work_arrangement='part_time'),created_by=f['actor'])
        assert await db.scalar(select(func.count(EmploymentEvent.id)))==0
        await create_employment_event(db,company_id=f['company'],contract_id=c.id,
            data=event(day=date(2026,3,1),work_arrangement='part_time'),created_by=f['actor'])


async def test_event_migration_and_immutable_history(employee_engine):
    path=Path(__file__).parents[2]/'alembic/versions/13d4a7b8c006_employment_events.py'
    spec=importlib.util.spec_from_file_location('employment_event_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    async with employee_engine.begin() as connection:
        await connection.execute(text('DROP TABLE employment_events'))
        def run(sync, method):
            with Operations.context(MigrationContext.configure(sync)):
                getattr(migration,method)()
        await connection.run_sync(run,'upgrade')
        await connection.run_sync(run,'downgrade')
        await connection.run_sync(run,'upgrade')
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db);c=await contract(db,f)
        row=await create_employment_event(db,company_id=f['company'],contract_id=c.id,
            data=event(work_arrangement='part_time'),created_by=f['actor'])
        for statement in ('UPDATE employment_events SET reason=\'changed\' WHERE id=:id',
                          'DELETE FROM employment_events WHERE id=:id'):
            with pytest.raises(Exception,match='immutable'):
                async with db.begin_nested():
                    await db.execute(text(statement),{'id':row.id})
        with pytest.raises(Exception,match='history must be preserved'):
            async with db.begin_nested():
                connection=await db.connection()
                await connection.run_sync(run,'downgrade')
