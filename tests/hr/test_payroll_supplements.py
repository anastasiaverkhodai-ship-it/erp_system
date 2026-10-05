"""Supplement provenance, attendance bounds and base/premium accounting."""
import importlib.util
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_employee_foundation import employee_engine
from test_payroll_source_locking import sources
from app.models.payroll import PayrollCalculationLine
from app.schemas.payroll import PayrollPeriodCreate, PayrollInputCreate
from app.schemas.payroll_supplement import PayrollSupplementCreate
from app.schemas.time_attendance import AttendanceCreate
from app.services.time_attendance_service import create_attendance
from app.services.payroll_period_service import create_payroll_period, finalize_payroll_period, PayrollPeriodLifecycleError
from app.services.payroll_input_service import create_payroll_input
from app.services.payroll_calculation_service import calculate_payroll_input
from app.services.payroll_supplement_service import (
    create_payroll_supplement, cancel_payroll_supplement,
    PayrollSupplementError, night_window_minutes, time_premium,
)


def test_night_windows_use_actual_time_and_dst():
    zone = ZoneInfo('Europe/Kyiv')
    assert night_window_minutes(datetime(2026,9,1,9,tzinfo=zone), datetime(2026,9,1,19,tzinfo=zone), str(zone)) == 0
    assert night_window_minutes(datetime(2026,9,1,21,tzinfo=zone), datetime(2026,9,2,7,tzinfo=zone), str(zone)) == 480
    assert night_window_minutes(datetime(2026,10,24,22,tzinfo=zone), datetime(2026,10,25,6,tzinfo=zone), str(zone)) == 540
    assert night_window_minutes(datetime(2026,3,28,22,tzinfo=zone), datetime(2026,3,29,6,tzinfo=zone), str(zone)) == 420


def test_premium_is_only_increment_above_base_salary():
    for kind, coefficient, expected in [('overtime','1','250'), ('rest_day','1','250'), ('night','.2','50'), ('regular_extra','1','0')]:
        _, amount = time_premium(kind=kind, minutes=120, coefficient=Decimal(coefficient),
            rate_type='monthly', salary_amount=Decimal('22000'), monthly_norm_minutes=10560)
        assert amount == Decimal(expected)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')
async def test_real_payroll_extra_time_classification_and_freeze(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, contract, _ = await sources(db)
        company, actor = f['company'], f['actor']
        period = await create_payroll_period(db, company_id=company, created_by=actor,
            data=PayrollPeriodCreate(year=2026,month=9,start_date=date(2026,9,1),end_date=date(2026,9,30)))
        for day in range(1,31):
            d = date(2026,9,day)
            if d.weekday()>4: continue
            await create_attendance(db, company_id=company, created_by=actor, data=AttendanceCreate(
                employment_contract_id=contract.id,work_date=d,status='present',
                actual_start_at=datetime(2026,9,day,9,tzinfo=ZoneInfo('Europe/Kyiv')),
                actual_end_at=datetime(2026,9,day,19 if day==1 else 17,tzinfo=ZoneInfo('Europe/Kyiv'))))
        source = await create_payroll_input(db,company_id=company,payroll_period_id=period.id,
            created_by=actor,data=PayrollInputCreate(employment_contract_id=contract.id))
        input_id, period_id = source.id, period.id
        with pytest.raises(PayrollPeriodLifecycleError,match='Classify'):
            await finalize_payroll_period(db,company_id=company,payroll_period_id=period_id,finalized_by=actor)
        async def add(kind, key, **kwargs):
            return await create_payroll_supplement(db,company_id=company,payroll_input_id=input_id,created_by=actor,
                data=PayrollSupplementCreate(kind=kind,request_key=key,work_date=date(2026,9,1),source_reference='Order 1',**kwargs))
        with pytest.raises(PayrollSupplementError,match='night window'):
            await add('night','invalid-night',minutes=60)
        with pytest.raises(PayrollSupplementError,match='non-working'):
            await add('rest_day','invalid-rest',minutes=120)
        overtime = await add('overtime','ot-1',minutes=120)
        overtime_id = overtime.id
        assert (await add('overtime','ot-1',minutes=120)).id == overtime_id
        with pytest.raises(PayrollSupplementError,match='Extra minutes exceed'):
            await add('overtime','ot-duplicate',minutes=120)
        await add('bonus','bonus-1',amount=Decimal('100'))
        await finalize_payroll_period(db,company_id=company,payroll_period_id=period_id,finalized_by=actor)
        calc = await calculate_payroll_input(db,company_id=company,payroll_input_id=input_id,calculated_by=actor)
        assert calc.gross_amount == Decimal('22600.00')  # 22000 + 250 base + 250 premium + 100 bonus
        lines = list((await db.scalars(select(PayrollCalculationLine).where(PayrollCalculationLine.payroll_calculation_id==calc.id))).all())
        assert sum(line.amount for line in lines if line.line_type=='supplement') == Decimal('350')
        assert {line.source_supplement_id for line in lines if line.line_type=='supplement'} >= {overtime_id}
        # A rollback of the feature must never silently erase recorded supplements.
        spec = importlib.util.spec_from_file_location('supplement_guard', Path('alembic/versions/13d4a7b8c008_payroll_earning_supplements.py'))
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        def downgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
        from sqlalchemy.exc import DBAPIError
        with pytest.raises(DBAPIError, match='Cannot downgrade payroll supplements with history'):
            async with db.begin_nested():
                await (await db.connection()).run_sync(downgrade)
        with pytest.raises(PayrollSupplementError,match='Finalized'):
            await cancel_payroll_supplement(db,company_id=company,supplement_id=overtime_id,cancelled_by=actor)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')
async def test_migration_replaces_checks(employee_engine):
    spec = importlib.util.spec_from_file_location('supplement_migration', Path('alembic/versions/13d4a7b8c008_payroll_earning_supplements.py'))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with employee_engine.begin() as conn:
        def roundtrip(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
                migration.upgrade()
        await conn.run_sync(roundtrip)
        checks = dict((await conn.execute(text("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='payroll_calculation_lines'::regclass AND contype='c'"))).all())
        for name in ('type','rate_nonnegative','amount_by_type'):
            assert 'supplement' in checks['ck_payroll_calculation_lines_'+name]
        assert 'source_supplement_id' in checks['ck_payroll_calculation_line_supplement_source']


def test_attendance_elapsed_time_matches_night_window_across_clock_change():
    from app.services.time_attendance_service import _worked_minutes
    from app.models.time_attendance import AttendanceStatus
    zone = ZoneInfo('Europe/Kyiv')
    for month, day, expected in [(10,24,540), (3,28,420)]:
        start, end = datetime(2026,month,day,22,tzinfo=zone), datetime(2026,month,day+1,6,tzinfo=zone)
        assert _worked_minutes(status=AttendanceStatus.PRESENT, actual_start_at=start,
            actual_end_at=end, break_minutes=0) == expected
    # The repeated 03:00 hour has an unambiguous elapsed duration.
    start = datetime(2026,10,25,3,30,tzinfo=zone,fold=0)
    end = datetime(2026,10,25,3,15,tzinfo=zone,fold=1)
    data = AttendanceCreate(employment_contract_id=1,work_date=date(2026,10,25),status='present',
        actual_start_at=start,actual_end_at=end)
    assert _worked_minutes(status=AttendanceStatus.PRESENT, actual_start_at=data.actual_start_at,
        actual_end_at=data.actual_end_at,break_minutes=0) == 45


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')
async def test_overnight_attendance_cannot_double_count_adjacent_date(employee_engine):
    from app.schemas.time_attendance import AttendanceUpdate
    from app.services.time_attendance_service import update_attendance, TimeAttendanceConflictError
    zone = ZoneInfo('Europe/Kyiv')
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, contract, _ = await sources(db)
        first = await create_attendance(db,company_id=f['company'],created_by=f['actor'],
            data=AttendanceCreate(employment_contract_id=contract.id,work_date=date(2026,9,1),status='present',
                actual_start_at=datetime(2026,9,1,22,tzinfo=zone),actual_end_at=datetime(2026,9,2,6,tzinfo=zone)))
        with pytest.raises(TimeAttendanceConflictError, match='overlaps'):
            await create_attendance(db,company_id=f['company'],created_by=f['actor'],
                data=AttendanceCreate(employment_contract_id=contract.id,work_date=date(2026,9,2),status='present',
                    actual_start_at=datetime(2026,9,2,5,tzinfo=zone),actual_end_at=datetime(2026,9,2,13,tzinfo=zone)))
        adjacent = await create_attendance(db,company_id=f['company'],created_by=f['actor'],
            data=AttendanceCreate(employment_contract_id=contract.id,work_date=date(2026,9,2),status='present',
                actual_start_at=datetime(2026,9,2,6,tzinfo=zone),actual_end_at=datetime(2026,9,2,14,tzinfo=zone)))
        assert adjacent.worked_minutes == 480
        with pytest.raises(TimeAttendanceConflictError, match='overlaps'):
            await update_attendance(db,company_id=f['company'],attendance_id=first.id,changed_by=f['actor'],
                data=AttendanceUpdate(actual_end_at=datetime(2026,9,2,7,tzinfo=zone)))
        assert first.worked_minutes == 480
