"""Vacation and sick-history days differ for hire on the first working day."""
import os
from datetime import date, time, datetime, UTC
from decimal import Decimal
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from app.models.leave_request import LeaveRequest
from app.schemas.time_attendance import WorkScheduleCreate, WorkScheduleDayInput, EmploymentScheduleAssignmentCreate
from app.services.time_attendance_service import create_work_schedule, create_schedule_assignment
from app.schemas.payroll_opening import PayrollEarningsHistoryInput
from app.services.payroll_opening_service import import_earnings_history, PayrollOpeningError
from app.services.payroll_average_history_service import derive_leave_average_history, calculate_vacation_from_history

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


@pytest.mark.parametrize('hire_day,first_month,days',[(3,8,61),(4,9,30)])
async def test_reference_month_from_actual_hire_calendar(employee_engine,hire_day,first_month,days):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract=await _seed_identity(db)
        contract.start_date=date(2026,8,hire_day)
        await db.flush()
        schedule=await create_work_schedule(db,company_id=company.id,created_by=user.id,
            data=WorkScheduleCreate(code='HIRE',name='Hiring calendar',timezone='Europe/Kyiv',days=[
                WorkScheduleDayInput(weekday=i,is_working_day=True,start_time=time(9),end_time=time(17)) for i in range(1,6)]))
        await create_schedule_assignment(db,company_id=company.id,contract_id=contract.id,created_by=user.id,
            data=EmploymentScheduleAssignmentCreate(work_schedule_id=schedule.id,effective_from=contract.start_date))
        leave=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type='annual',
            start_date=date(2026,10,1),end_date=date(2026,10,5),status='approved',requested_by=user.id,
            approved_by=user.id,approved_at=datetime.now(UTC))
        db.add(leave);await db.flush()
        august_days=31 if hire_day==3 else 28
        for month,eligible_days,sick_days in [(8,august_days,32-hire_day),(9,30,30)]:
            await import_earnings_history(db,company_id=company.id,contract_id=contract.id,created_by=user.id,
                data=PayrollEarningsHistoryInput(month=date(2026,month,1),cutover_date=date(2026,10,1),
                    gross_amount=Decimal(eligible_days)*100,vacation_earnings=Decimal(eligible_days)*100,
                    sick_earnings=Decimal(eligible_days)*100,vacation_days=eligible_days,sick_days=sick_days,
                    source_reference='Verified register'))
        basis=await derive_leave_average_history(db,company_id=company.id,leave_request_id=leave.id)
        assert basis['reference_period_start']==date(2026,first_month,1)
        assert basis['eligible_days']==days
        assert basis['eligible_earnings']==days*100
        result=await calculate_vacation_from_history(db,company_id=company.id,leave_request_id=leave.id,calculated_by=user.id)
        assert result.vacation_pay_amount==500
        assert result.rule_version=='5-bonus-allocation-4928-IX'
        assert (await calculate_vacation_from_history(db,company_id=company.id,
            leave_request_id=leave.id,calculated_by=user.id)).id==result.id


async def test_first_working_day_does_not_expand_sick_history_days(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract=await _seed_identity(db)
        contract.start_date=date(2026,8,3);await db.flush()
        with pytest.raises(PayrollOpeningError,match='exceed employment'):
            await import_earnings_history(db,company_id=company.id,contract_id=contract.id,created_by=user.id,
                data=PayrollEarningsHistoryInput(month=date(2026,8,1),cutover_date=date(2026,9,1),
                    gross_amount=2900,vacation_earnings=2900,sick_earnings=2900,
                    vacation_days=29,sick_days=31,source_reference='Invalid sick days'))
