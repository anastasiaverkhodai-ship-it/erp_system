"""The documented limited-service flag must affect the actual payable benefit."""
import os
from datetime import date, datetime, UTC
from decimal import Decimal as D
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from app.models.leave_request import LeaveRequest
from app.services.payroll_sick_leave_service import calculate_sick_leave_pay, PayrollSickLeaveDerivationError

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1', reason='PostgreSQL required')]


@pytest.mark.parametrize('average,percent,limited,daily', [
    ('1000','50',True,'284.07'),
    ('400','50',True,'200.00'),
    ('1000','50',False,'500.00'),
    ('284.07','100',True,'284.07'),
])
async def test_limited_service_caps_benefit_after_percentage(employee_engine,average,percent,limited,daily):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,_,contract=await _seed_identity(db)
        leave=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,
            leave_type='sick',start_date=date(2026,9,26),end_date=date(2026,10,5),
            status='approved',requested_by=user.id,approved_by=user.id,approved_at=datetime.now(UTC))
        db.add(leave);await db.flush()
        args=dict(company_id=company.id,leave_request_id=leave.id,
            benefit_case_code='ORDINARY_TEMPORARY_INCAPACITY',reference_period_start=date(2026,1,1),
            reference_period_end=date(2026,8,31),eligible_earnings=D(average)*243,eligible_days=D(243),
            insurance_service_months=24,benefit_percent=D(percent),limited_service_rule_applied=limited,
            employer_days=D(5),insurer_days=D(5),rule_code='UA_1105_12_4',rule_version='2026',calculated_by=user.id)
        row=await calculate_sick_leave_pay(db,**args)
        assert row.daily_benefit_amount==D(daily)
        assert row.employer_amount==row.insurer_amount==D(daily)*5
        assert row.sick_pay_amount==D(daily)*10
        assert (await calculate_sick_leave_pay(db,**args)).id==row.id
        with pytest.raises(PayrollSickLeaveDerivationError, match='nonnegative integer'):
            await calculate_sick_leave_pay(db,**dict(args,insurance_service_months=D('24.5')))



async def test_sick_history_excludes_partial_hire_month_even_first_workday(employee_engine):
    from app.schemas.payroll_opening import PayrollEarningsHistoryInput
    from app.services.payroll_opening_service import import_earnings_history
    from app.services.payroll_average_history_service import derive_leave_average_history
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,_,contract=await _seed_identity(db)
        contract.start_date=date(2026,8,3)
        leave=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,
            leave_type='sick',start_date=date(2026,10,5),end_date=date(2026,10,9),
            status='approved',requested_by=user.id,approved_by=user.id,approved_at=datetime.now(UTC))
        db.add(leave);await db.flush()
        history=await import_earnings_history(db,company_id=company.id,contract_id=contract.id,created_by=user.id,
            data=PayrollEarningsHistoryInput(month=date(2026,9,1),cutover_date=date(2026,10,1),
                gross_amount=D(30000),vacation_earnings=D(30000),sick_earnings=D(30000),
                vacation_days=D(30),sick_days=D(30),source_reference='September earnings register'))
        basis=await derive_leave_average_history(db,company_id=company.id,leave_request_id=leave.id)
        assert basis['reference_period_start']==date(2026,9,1)
        assert basis['reference_period_end']==date(2026,9,30)
        assert basis['eligible_days']==30
        assert basis['average_daily_amount']==1000
        assert [s['source_id'] for s in basis['sources']]==[history.id]
