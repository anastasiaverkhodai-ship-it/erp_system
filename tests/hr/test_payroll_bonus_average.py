import os
from datetime import date, datetime, UTC
from decimal import Decimal as D
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_average_history import seed
from test_payroll_average_classification import month
from app.models.payroll import PayrollCalculationLine
from app.models.payroll_supplement import PayrollSupplement
from app.models.time_attendance import AttendanceRecord
from app.services.payroll_bonus_average_service import allocate_bonus, allocation_months
from app.services.payroll_average_history_service import derive_leave_average_history, calculate_vacation_from_history
from app.services.payroll_vacation_service import list_vacation_calculation_sources
from app.schemas.payroll_supplement import BonusAverageBasis, PayrollSupplementCreate


def test_bonus_allocation_full_and_partial_period():
    months = {date(2026,7,1):20, date(2026,8,1):21, date(2026,9,1):22}
    assert sum(allocate_bonus(amount=6000,worked_days=60,planned_days=60,target_worked_days=months).values()) == 6300
    assert sum(allocate_bonus(amount=6000,worked_days=30,planned_days=60,target_worked_days=months).values()) == 6000
    # Cap applies only to included reference months, not unseen earlier months.
    assert allocate_bonus(amount=6000,worked_days=30,planned_days=60,
                          target_worked_days={date(2026,9,1):22}) == {date(2026,9,1):D(4400)}
    assert allocation_months(date(2026,11,1),date(2026,7,1),date(2026,9,30)) == [
        date(2026,11,1),date(2026,12,1),date(2027,1,1)]


def test_bonus_document_validation():
    from pydantic import ValidationError
    basis = dict(period_start='2026-07-01',period_end='2026-07-31',
                 worked_days=10,planned_days=20,source_reference='July register')
    assert BonusAverageBasis(**basis).worked_days == 10
    for changed in [dict(worked_days=21),dict(worked_days=0),dict(planned_days=32),
                    dict(period_end='2026-07-30'),dict(worked_days=True)]:
        with pytest.raises(ValidationError):
            BonusAverageBasis(**dict(basis,**changed))
    with pytest.raises(ValidationError):
        PayrollSupplementCreate(kind='holiday_bonus',work_date=date(2026,8,31),
            amount=1000,source_reference='Order',request_key='wrong-kind',average_basis=basis)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Isolated PostgreSQL')
@pytest.mark.parametrize('planned,expected',[(20,D('1000')),(10,D('2100'))])
async def test_average_allocates_bonus_once_and_preserves_evidence(employee_engine,planned,expected):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,_=await seed(db,erp_month=True)
        source,calc=await month(db,company,user,contract,4100)
        bonus=PayrollSupplement(company_id=company.id,payroll_input_id=source.id,kind='bonus',
            work_date=date(2026,8,31),minutes=0,coefficient=1,amount=1000,
            source_reference='July performance order',request_key='bonus',request_fingerprint='bonus',created_by=user.id,
            average_basis=dict(period_start='2026-07-01',period_end='2026-07-31',
                worked_days=10,planned_days=planned,source_reference='Verified July working-day register'))
        db.add(bonus);await db.flush()
        db.add(PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calc.id,line_no=2,
            line_type='supplement',quantity=1,rate=1000,amount=1000,currency_code='UAH',source_supplement_id=bonus.id))
        for number in range(1,32):
            day=date(2026,8,number)
            if day.weekday()<5:
                db.add(AttendanceRecord(company_id=company.id,employment_contract_id=contract.id,work_date=day,
                    status='present',source='manual',actual_start_at=datetime(2026,8,number,9,tzinfo=UTC),
                    actual_end_at=datetime(2026,8,number,17,tzinfo=UTC),break_minutes=0,worked_minutes=480,
                    created_by=user.id))
        await db.flush()
        args=dict(company_id=company.id,leave_request_id=leave.id)
        basis=await derive_leave_average_history(db,**args)
        assert basis['eligible_earnings']==36500+expected
        assert basis['eligible_days']==365
        from app.services.payroll_leave_compensation_basis_service import derive_compensation_average
        compensation = await derive_compensation_average(db,company_id=company.id,
            employment_contract_id=contract.id,compensation_date=leave.start_date,
            acquisition_bucket='from_2024')
        assert compensation['eligible_earnings']==36500+expected
        assert compensation['eligible_days']==365
        allocated=[s for s in basis['sources'] if s['source_type']=='bonus_allocation']
        assert len(allocated)==1 and allocated[0]['earnings_amount']==expected
        assert allocated[0]['basis_snapshot']['allocations'][0]['worked_days']==21
        result=await calculate_vacation_from_history(db,**args,calculated_by=user.id)
        saved=await list_vacation_calculation_sources(db,company_id=company.id,vacation_calculation_id=result.id)
        assert sum(s.earnings_amount for s in saved)==36500+expected
        assert [s for s in saved if s.source_type=='bonus_allocation'][0].basis_snapshot==allocated[0]['basis_snapshot']
        assert (await calculate_vacation_from_history(db,**args,calculated_by=user.id)).id==result.id
        await db.commit()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Isolated PostgreSQL')
async def test_bonus_before_reference_window_is_carried_forward(employee_engine):
    from app.models.payroll import PayrollPeriod
    from app.services.payroll_bonus_average_service import bonus_average_sources
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,_=await seed(db,erp_month=True)
        source,calc=await month(db,company,user,contract,4100)
        period=await db.get(PayrollPeriod,calc.payroll_period_id)
        period.month=7;period.start_date=date(2026,7,1);period.end_date=date(2026,7,31)
        await db.flush()
        _,target=await month(db,company,user,contract,3100)
        bonus=PayrollSupplement(company_id=company.id,payroll_input_id=source.id,kind='bonus',
            work_date=date(2026,7,31),minutes=0,coefficient=1,amount=1000,
            source_reference='Quarterly award',request_key='quarter',request_fingerprint='quarter',created_by=user.id,
            average_basis=dict(period_start='2026-04-01',period_end='2026-06-30',
                worked_days=50,planned_days=60,source_reference='Verified quarterly register'))
        db.add(bonus);await db.flush()
        db.add(PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calc.id,line_no=2,
            line_type='supplement',quantity=1,rate=1000,amount=1000,currency_code='UAH',source_supplement_id=bonus.id))
        db.add(AttendanceRecord(company_id=company.id,employment_contract_id=contract.id,work_date=date(2026,8,3),
            status='present',source='manual',actual_start_at=datetime(2026,8,3,9,tzinfo=UTC),
            actual_end_at=datetime(2026,8,3,17,tzinfo=UTC),break_minutes=0,worked_minutes=480,created_by=user.id))
        await db.flush()
        args=dict(company_id=company.id,contract_id=contract.id,start=date(2026,8,1),end=date(2026,8,31),
                  sources=[dict(source_type='payroll_calculation',source_id=target.id,source_period_start=date(2026,8,1))])
        result=await bonus_average_sources(db,**args)
        assert len(result)==1 and result[0]['earnings_amount']==20
        assert result[0]['basis_snapshot']['payroll_calculation_id']==calc.id
        assert await bonus_average_sources(db,**dict(args,company_id=2147483647))==[]
