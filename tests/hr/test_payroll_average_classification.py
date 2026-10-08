import os
from datetime import date, datetime, UTC
from decimal import Decimal as D
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_average_history import seed
from app.models.payroll import PayrollPeriod, PayrollInput, PayrollCalculation, PayrollCalculationLine
from app.models.payroll_supplement import PayrollSupplement
from app.models.leave_request import LeaveRequest
from app.services.payroll_average_history_service import derive_leave_average_history, PayrollAverageHistoryError

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')]


async def month(db, company, user, contract, amount=3100):
    period=PayrollPeriod(company_id=company.id,year=2026,month=8,start_date=date(2026,8,1),
        end_date=date(2026,8,31),status='finalized',created_by=user.id,
        finalized_by=user.id,finalized_at=datetime.now(UTC))
    db.add(period);await db.flush()
    source=PayrollInput(company_id=company.id,payroll_period_id=period.id,
        employment_contract_id=contract.id,created_by=user.id)
    db.add(source);await db.flush()
    calc=PayrollCalculation(company_id=company.id,payroll_period_id=period.id,payroll_input_id=source.id,
        employment_contract_id=contract.id,gross_amount=amount,currency_code='UAH',calculated_by=user.id)
    db.add(calc);await db.flush()
    line=PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calc.id,line_no=1,
        line_type='salary',quantity=1,rate=3100,amount=3100,currency_code='UAH')
    db.add(line);await db.flush()
    return source,calc


async def test_supplements_included_and_one_offs_excluded(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,_=await seed(db,erp_month=True)
        source,calc=await month(db,company,user,contract,3700)
        args=dict(company_id=company.id,leave_request_id=leave.id)
        supplements=[]
        for n,kind in enumerate(('night','overtime','rest_day','regular_extra','holiday_bonus','hardship'),start=2):
            item=PayrollSupplement(company_id=company.id,payroll_input_id=source.id,kind=kind,
                work_date=date(2026,8,10),minutes=60,coefficient=1,amount=100,
                source_reference='Documented '+kind,request_key=kind,request_fingerprint=kind,created_by=user.id)
            db.add(item);await db.flush();supplements.append(item)
            db.add(PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calc.id,line_no=n,
                line_type='supplement',quantity=1,rate=100,amount=100,currency_code='UAH',source_supplement_id=item.id))
        await db.flush()
        basis=await derive_leave_average_history(db,**args)
        assert basis['eligible_earnings']==36900
        assert basis['eligible_days']==365
        assert 'excluded one-off earnings 200.00' in basis['sources'][-1]['source_reference']
        supplements[0].kind='bonus';await db.flush()
        with pytest.raises(PayrollAverageHistoryError,match='Performance bonus'):
            await derive_leave_average_history(db,**args)
        supplements[0].kind='night'
        supplements[0].cancelled_at=datetime.now(UTC);supplements[0].cancelled_by=user.id
        await db.flush()
        with pytest.raises(PayrollAverageHistoryError,match='cancelled'):
            await derive_leave_average_history(db,**args)


@pytest.mark.parametrize('kind',['annual','sick'])
async def test_paid_absence_requires_exact_monthly_allocation(employee_engine,kind):
    from app.services.payroll_vacation_service import calculate_vacation_pay
    from app.services.payroll_sick_leave_service import calculate_sick_leave_pay
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,_=await seed(db,erp_month=True)
        paid=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type=kind,
            start_date=date(2026,7,30),end_date=date(2026,8,3),status='approved',requested_by=user.id,
            approved_by=user.id,approved_at=datetime.now(UTC))
        db.add(paid);await db.flush()
        common=dict(company_id=company.id,leave_request_id=paid.id,
            reference_period_start=date(2025,7,1),reference_period_end=date(2026,6,30),
            eligible_earnings=D('36500'),eligible_days=D('365'),rule_code='FIXTURE',rule_version='1',calculated_by=user.id)
        if kind=='annual':
            pay=await calculate_vacation_pay(db,**common)
            source_field='source_vacation_calculation_id';line_type='vacation_pay'
        else:
            pay=await calculate_sick_leave_pay(db,**common,benefit_case_code='ordinary',insurance_service_months=120,
                benefit_percent=D('100'),limited_service_rule_applied=False,employer_days=D('5'),insurer_days=D('0'))
            source_field='source_sick_leave_calculation_id';line_type='sick_pay'
        _,calc=await month(db,company,user,contract,3400)
        line=PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calc.id,line_no=2,
            line_type=line_type,quantity=3,rate=100,amount=300,currency_code='UAH',**{source_field:pay.id})
        db.add(line);await db.flush()
        args=dict(company_id=company.id,leave_request_id=leave.id)
        basis=await derive_leave_average_history(db,**args)
        assert basis['eligible_earnings']==36800 and basis['eligible_days']==365
        # The source totals 500, but only 300 belongs to August.
        line.amount=500;calc.gross_amount=3600;await db.flush()
        with pytest.raises(PayrollAverageHistoryError,match='monthly source allocation'):
            await derive_leave_average_history(db,**args)
        line.amount=300;calc.gross_amount=3400
        paid.status='cancelled';paid.cancelled_by=user.id;paid.cancelled_at=datetime.now(UTC)
        await db.flush()
        with pytest.raises(PayrollAverageHistoryError,match='monthly source allocation'):
            await derive_leave_average_history(db,**args)


async def test_previous_rule_snapshot_remains_immutable(employee_engine):
    from app.services.payroll_average_history_service import calculate_vacation_from_history
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,histories=await seed(db)
        args=dict(company_id=company.id,leave_request_id=leave.id,calculated_by=user.id)
        original=await calculate_vacation_from_history(db,**args)
        original.rule_version='1-calendar-4928-IX'
        await db.flush()
        # A later source correction must not silently replace the saved result.
        histories[0].vacation_earnings=D('1')
        await db.flush()
        repeated=await calculate_vacation_from_history(db,**args)
        assert repeated.id==original.id
        assert repeated.vacation_pay_amount==700
        assert repeated.rule_version=='1-calendar-4928-IX'
