"""Exercise real sources, finalization and immutable payroll snapshots."""
import asyncio
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_employment_structure import setup, contract
from app.schemas.time_attendance import (
    WorkScheduleCreate, WorkScheduleDayInput, EmploymentScheduleAssignmentCreate,
    AttendanceCreate, AttendanceUpdate,
)
from app.schemas.employee_salary_rate import SalaryRateCreate
from app.schemas.payroll import PayrollPeriodCreate, PayrollInputCreate
from app.services.time_attendance_service import (
    create_work_schedule, create_schedule_assignment, create_attendance,
    update_attendance, TimeAttendanceLifecycleError, TimeAttendanceConflictError,
)
from app.services.employee_salary_rate_service import create_salary_rate
from app.services.payroll_period_service import create_payroll_period, finalize_payroll_period
from app.services.payroll_input_service import create_payroll_input
from app.services.payroll_calculation_service import calculate_payroll_input

pytestmark=[pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]

async def sources(db):
    f=await setup(db)
    c=await contract(db,f)
    schedule=await create_work_schedule(db,company_id=f['company'],created_by=f['actor'],
        data=WorkScheduleCreate(code='WEEK',name='Week',timezone='Europe/Kyiv',days=[
            WorkScheduleDayInput(weekday=i,is_working_day=True,start_time=time(9),end_time=time(17))
            for i in range(1,6)]))
    await create_schedule_assignment(db,company_id=f['company'],contract_id=c.id,created_by=f['actor'],
        data=EmploymentScheduleAssignmentCreate(work_schedule_id=schedule.id,effective_from=c.start_date))
    await create_salary_rate(db,company_id=f['company'],created_by=f['actor'],data=SalaryRateCreate(
        employment_contract_id=c.id,rate_type='monthly',amount=Decimal('22000'),currency_code='UAH',effective_from=c.start_date))
    return f,c,schedule

async def test_finalize_refreshes_sources_and_prevents_late_attendance_changes(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,c,_=await sources(db)
        period=await create_payroll_period(db,company_id=f['company'],created_by=f['actor'],
            data=PayrollPeriodCreate(year=2026,month=9,start_date=date(2026,9,1),end_date=date(2026,9,30)))
        first=None
        for day in range(1,31):
            d=date(2026,9,day)
            if d.weekday()>4: continue
            row=await create_attendance(db,company_id=f['company'],created_by=f['actor'],
                data=AttendanceCreate(employment_contract_id=c.id,work_date=d,status='absent'))
            if first is None:first=row
        inputs=await create_payroll_input(db,company_id=f['company'],payroll_period_id=period.id,
            created_by=f['actor'],data=PayrollInputCreate(employment_contract_id=c.id))
        assert inputs.worked_minutes==0
        await update_attendance(db,company_id=f['company'],attendance_id=first.id,changed_by=f['actor'],
            data=AttendanceUpdate(status='present',actual_start_at=datetime(2026,9,1,9,tzinfo=ZoneInfo('Europe/Kyiv')),
                actual_end_at=datetime(2026,9,1,17,tzinfo=ZoneInfo('Europe/Kyiv'))))
        await finalize_payroll_period(db,company_id=f['company'],payroll_period_id=period.id,finalized_by=f['actor'])
        assert inputs.worked_minutes==480
        calc=await calculate_payroll_input(db,company_id=f['company'],payroll_input_id=inputs.id,calculated_by=f['actor'])
        assert calc.gross_amount==Decimal('1000.00')
        with pytest.raises(TimeAttendanceLifecycleError,match='finalized'):
            async with db.begin_nested():
                await update_attendance(db,company_id=f['company'],attendance_id=first.id,
                    data=AttendanceUpdate(status='absent'),changed_by=f['actor'])

async def test_concurrent_overlapping_schedule_assignments_are_rejected(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db); c=await contract(db,f)
        schedule=await create_work_schedule(db,company_id=f['company'],created_by=f['actor'],
            data=WorkScheduleCreate(code='A',name='A',timezone='Europe/Kyiv'))
        cid,sid=c.id,schedule.id
        await db.commit()
    async def assign():
        async with AsyncSession(employee_engine) as db:
            try:
                await create_schedule_assignment(db,company_id=f['company'],contract_id=cid,created_by=f['actor'],
                    data=EmploymentScheduleAssignmentCreate(work_schedule_id=sid,effective_from=date(2026,9,1)))
                await db.commit();return 'created'
            except TimeAttendanceConflictError:
                await db.rollback();return 'conflict'
    assert sorted(await asyncio.gather(assign(),assign()))==['conflict','created']

async def test_mid_month_hire_uses_full_month_norm(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db)
        c=await contract(db,f,start_date=date(2026,9,16))
        schedule=await create_work_schedule(db,company_id=f['company'],created_by=f['actor'],
            data=WorkScheduleCreate(code='W',name='Week',timezone='Europe/Kyiv',days=[
                WorkScheduleDayInput(weekday=i,is_working_day=True,start_time=time(9),end_time=time(17))
                for i in range(1,6)]))
        await create_schedule_assignment(db,company_id=f['company'],contract_id=c.id,created_by=f['actor'],
            data=EmploymentScheduleAssignmentCreate(work_schedule_id=schedule.id,effective_from=c.start_date))
        await create_salary_rate(db,company_id=f['company'],created_by=f['actor'],data=SalaryRateCreate(
            employment_contract_id=c.id,rate_type='monthly',amount=Decimal('22000'),currency_code='UAH',effective_from=c.start_date))
        period=await create_payroll_period(db,company_id=f['company'],created_by=f['actor'],
            data=PayrollPeriodCreate(year=2026,month=9,start_date=date(2026,9,1),end_date=date(2026,9,30)))
        for day in range(16,31):
            d=date(2026,9,day)
            if d.weekday()>4: continue
            await create_attendance(db,company_id=f['company'],created_by=f['actor'],data=AttendanceCreate(
                employment_contract_id=c.id,work_date=d,status='present',
                actual_start_at=datetime(2026,9,day,9,tzinfo=ZoneInfo('Europe/Kyiv')),
                actual_end_at=datetime(2026,9,day,17,tzinfo=ZoneInfo('Europe/Kyiv'))))
        inputs=await create_payroll_input(db,company_id=f['company'],payroll_period_id=period.id,
            created_by=f['actor'],data=PayrollInputCreate(employment_contract_id=c.id))
        assert inputs.monthly_norm_minutes==10560
        assert inputs.worked_minutes==5280
        await finalize_payroll_period(db,company_id=f['company'],payroll_period_id=period.id,finalized_by=f['actor'])
        calc=await calculate_payroll_input(db,company_id=f['company'],payroll_input_id=inputs.id,calculated_by=f['actor'])
        assert calc.gross_amount==Decimal('11000.00')

async def test_norm_migration_preserves_existing_tables(employee_engine):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    path=Path('alembic/versions/13d4a7b8c001_payroll_monthly_norm.py')
    spec=importlib.util.spec_from_file_location('norm_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    async with employee_engine.begin() as conn:
        before=(await conn.execute(text('SELECT to_jsonb(t) FROM companies t'))).scalars().all()
        def roundtrip(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
                migration.upgrade()
        await conn.run_sync(roundtrip)
        assert (await conn.execute(text('SELECT to_jsonb(t) FROM companies t'))).scalars().all()==before
        assert await conn.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='payroll_inputs' AND column_name='monthly_norm_minutes'"))==1
