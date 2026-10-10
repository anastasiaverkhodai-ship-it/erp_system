"""Partial calendar months use actual schedules for the employee-wide USC floor."""
import os
from datetime import date, time, datetime, UTC
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_employee_tax_base import setup_month
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollPeriod, PayrollInput
from app.models.payroll_statutory import PayrollStatutoryResultLine
from app.schemas.time_attendance import (
    WorkScheduleCreate, WorkScheduleDayInput, EmploymentScheduleAssignmentCreate, AttendanceCreate,
)
from app.services.time_attendance_service import (
    create_work_schedule, create_schedule_assignment, create_attendance,
)
from app.services.payroll_statutory_service import calculate_payroll_statutory_result

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')]


@pytest.mark.parametrize('hire_day,end_day,missing_day,expected,single', [
    (3, None, None, '1760', False),  # Aug 1-2 are weekends; every working day is worked.
    (4, None, None, '330', False),   # A scheduled Monday is outside employment.
    (3, None, 12, '330', False),    # Not all working days worked in partial calendar month.
    (1, 28, None, '330', False),    # Monday Aug 31 remains outside employment.
    (3, 31, None, '1760', False),
    (3, None, None, '1760', True),
    (4, None, None, '220', True),
])
async def test_partial_primary_month_floor(employee_engine, hire_day, end_day, missing_day, expected, single):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, user, calculations = await setup_month(db, ('1000','500'), '8000', '10000')
        if single:
            secondary = calculations.pop()
            secondary_input = await db.get(PayrollInput, secondary.payroll_input_id)
            secondary_contract = await db.get(EmploymentContract, secondary.employment_contract_id)
            await db.delete(secondary)
            await db.flush()
            await db.delete(secondary_input)
            await db.flush()
            await db.delete(secondary_contract)
            await db.flush()
        primary = await db.get(EmploymentContract, calculations[0].employment_contract_id)
        primary.start_date = date(2026,8,hire_day)
        primary.end_date = date(2026,8,end_day) if end_day else None
        primary.status = 'ended' if end_day else 'active'
        period = await db.get(PayrollPeriod, calculations[0].payroll_period_id)
        period.month = 8
        period.start_date, period.end_date = date(2026,8,1), date(2026,8,31)
        period.status, period.finalized_by, period.finalized_at = 'draft', None, None
        await db.flush()
        schedule = await create_work_schedule(db, company_id=company.id, created_by=user.id,
            data=WorkScheduleCreate(code='USC', name='USC calendar', timezone='Europe/Kyiv', days=[
                WorkScheduleDayInput(weekday=i, is_working_day=True, start_time=time(9), end_time=time(17))
                for i in range(1,6)]))
        await create_schedule_assignment(db, company_id=company.id, contract_id=primary.id,
            created_by=user.id, data=EmploymentScheduleAssignmentCreate(
                work_schedule_id=schedule.id, effective_from=primary.start_date, effective_to=primary.end_date))
        for day in range(hire_day,(end_day or 31)+1):
            current=date(2026,8,day)
            if current.weekday()>4:
                continue
            missing=day==missing_day
            await create_attendance(db, company_id=company.id, created_by=user.id,
                data=AttendanceCreate(employment_contract_id=primary.id, work_date=current,
                    status='absent' if missing else 'present',
                    actual_start_at=None if missing else datetime(2026,8,day,9,tzinfo=ZoneInfo('Europe/Kyiv')),
                    actual_end_at=None if missing else datetime(2026,8,day,17,tzinfo=ZoneInfo('Europe/Kyiv'))))
        period.status, period.finalized_by, period.finalized_at='finalized', user.id, datetime.now(UTC)
        await db.flush()
        total=Decimal(0)
        for calculation in reversed(calculations):
            result=await calculate_payroll_statutory_result(db, company_id=company.id,
                payroll_calculation_id=calculation.id, actor_user_id=user.id)
            line=await db.scalar(select(PayrollStatutoryResultLine).where(
                PayrollStatutoryResultLine.payroll_statutory_result_id==result.id,
                PayrollStatutoryResultLine.component=='unified_social_contribution'))
            total+=line.amount
        assert total==Decimal(expected)
