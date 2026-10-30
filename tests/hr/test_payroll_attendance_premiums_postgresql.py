import os
from datetime import date,datetime,time
from decimal import Decimal as D
from zoneinfo import ZoneInfo
from importlib.util import spec_from_file_location,module_from_spec
from unittest.mock import patch
import pytest
from sqlalchemy import select,text
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_attendance_correction_postgresql import install_attendance
from test_payroll_leave_correction_financial_postgresql import setup
from app.models.payroll import PayrollCalculation
from app.models.time_attendance import AttendanceRecord
from app.models.payroll_supplement import PayrollSupplement
from app.schemas.payroll_attendance_correction import AttendanceCorrectionCreate
from app.schemas.time_attendance import WorkScheduleCreate,WorkScheduleDayInput,EmploymentScheduleAssignmentCreate
from app.services.time_attendance_service import create_work_schedule,create_schedule_assignment
from app.services.payroll_attendance_correction_service import request_attendance_correction,apply_attendance_correction
from app.services.payroll_disbursement_service import create_payroll_disbursement,generate_and_post_payroll_disbursement_journal
from app.services.payroll_sick_settlement_service import sick_settlement_summary
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]

async def test_reviewed_night_overtime_recalculates_salary_taxes_and_due(employee_engine):
    await install_attendance(employee_engine)
    from alembic import op
    statements=[]
    spec=spec_from_file_location('premiums068','alembic/versions/13d4a7b8c068_attendance_time_premiums.py')
    migration=module_from_spec(spec);spec.loader.exec_module(migration)
    with patch.object(op,'add_column'),patch.object(op,'create_foreign_key'),patch.object(op,'execute',lambda sql:statements.append(sql)):
        migration.upgrade()
    async with employee_engine.begin() as conn:
        for statement in statements:await conn.execute(text(statement))
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,reviewer,source,calc,bank,_=await setup(db,with_salary=True)
        schedule=await create_work_schedule(db,company_id=company.id,created_by=user.id,
            data=WorkScheduleCreate(code='CORR',name='Verified work calendar',timezone='Europe/Kyiv',days=[
                WorkScheduleDayInput(weekday=i,is_working_day=True,start_time=time(9),end_time=time(17)) for i in range(1,6)]))
        await create_schedule_assignment(db,company_id=company.id,contract_id=calc.employment_contract_id,created_by=user.id,
            data=EmploymentScheduleAssignmentCreate(work_schedule_id=schedule.id,effective_from=date(2026,9,1)))
        db.add(AttendanceRecord(company_id=company.id,employment_contract_id=calc.employment_contract_id,
            work_date=date(2026,9,11),status='absent',worked_minutes=0,source='manual',created_by=user.id))
        await db.flush()
        payout=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=calc.id,
            bank_account_id=bank.id,payment_date=date(2026,9,30),created_by=user.id,request_key='original')
        await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=payout.id,created_by=user.id)
        zone=ZoneInfo('Europe/Kyiv')
        for index,minutes,end,premiums,gross,due in [
            (0,600,datetime(2026,9,12,8,tzinfo=zone),[dict(kind='night',minutes=480,coefficient='.2'),dict(kind='overtime',minutes=120)],13700,1309),
            (1,240,datetime(2026,9,12,2,tzinfo=zone),[dict(kind='night',minutes=240,coefficient='.2')],12600,462),
        ]:
            request=await request_attendance_correction(db,company_id=company.id,actor_id=user.id,
                data=AttendanceCorrectionCreate(calculation_id=calc.id,correction_date=date(2026,10,28+index),
                    request_key=f'time:{index}',reason='Verified corrected shift and premiums',document_reference='Signed actual attendance order',
                    days=[dict(work_date=date(2026,9,11),status='present',worked_minutes=minutes,
                        actual_start_at=datetime(2026,9,11,22,tzinfo=zone),actual_end_at=end,premiums=premiums)]))
            app=await apply_attendance_correction(db,company_id=company.id,request_id=request.id,actor_id=reviewer.id)
            calc=await db.get(PayrollCalculation,app.payroll_calculation_ids[0])
            assert calc.gross_amount==gross
            state=await sick_settlement_summary(db,company_id=company.id,calculation_id=calc.id,as_of=date(2026,10,28+index))
            assert state['employee_due']==due
            await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            await db.execute(text('SET CONSTRAINTS ALL DEFERRED'))
        rows=list((await db.scalars(select(PayrollSupplement).where(PayrollSupplement.company_id==company.id))).all())
        assert len(rows)==3 and all(row.correction_request_id for row in rows)
        original=await db.scalar(select(AttendanceRecord).where(AttendanceRecord.company_id==company.id,AttendanceRecord.work_date==date(2026,9,11)))
        assert original.worked_minutes==0
