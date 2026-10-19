import os
from datetime import date,timedelta
from decimal import Decimal as D
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from app.models.leave_request import LeaveRequest
from app.schemas.payroll_leave_balance import LeaveAccountCreate,LeaveAccrualCreate
from app.services.payroll_leave_balance_service import create_leave_account,accrue_leave_days,allocate_leave_days,leave_balances,LeaveBalanceError
from app.services.leave_request_service import cancel_leave_request

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Isolated PostgreSQL')]


async def setup(db,leave_start,days=24):
    company,user,employee,contract=await _seed_identity(db)
    contract.start_date=date(2026,1,1)
    await db.flush()
    account=await create_leave_account(db,company_id=company.id,actor_id=user.id,
        data=LeaveAccountCreate(employment_contract_id=contract.id,leave_kind='annual',entitlement_code='basic',
            period_start=date(2026,1,1),period_end=date(2026,12,31),
            segments=[dict(start=date(2026,1,1),end=date(2026,12,31),annual_days=24)],
            source_reference='Employment terms',request_key='account'))
    await accrue_leave_days(db,company_id=company.id,account_id=account.id,actor_id=user.id,
        data=LeaveAccrualCreate(through=leave_start-timedelta(days=1),
            source_reference='Service through preceding day',request_key='accrual'))
    leave=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,
        leave_type='annual',start_date=leave_start,end_date=leave_start+timedelta(days=days-1),
        status='pending',requested_by=user.id)
    db.add(leave);await db.flush()
    return company,user,contract,account,leave


async def test_authorized_advance_is_bounded_idempotent_and_reversible(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,account,leave=await setup(db,date(2026,7,1))
        args=dict(company_id=company.id,leave_request_id=leave.id,request_key='advance',
                  source_reference='Approved annual leave order',actor_id=user.id)
        allocation=dict(account_id=account.id,bucket='from_2024',days=D(24))
        with pytest.raises(LeaveBalanceError,match='advance authorization'):
            await allocate_leave_days(db,**args,allocations=[allocation])
        authorization=dict(reason='six_months',document_date=date(2026,6,30),source_reference='Approved schedule and request')
        allocation['advance_authorization']=authorization
        saved=await allocate_leave_days(db,**args,allocations=[allocation])
        assert saved[0].advance_basis['authorization']['reason']=='six_months'
        assert D(saved[0].advance_basis['borrowed_days'])>0
        assert (await allocate_leave_days(db,**args,allocations=[allocation]))[0].id==saved[0].id
        altered=dict(allocation,advance_authorization=dict(authorization,source_reference='Different order'))
        with pytest.raises(LeaveBalanceError,match='different'):
            await allocate_leave_days(db,**args,allocations=[altered])
        balances=await leave_balances(db,company_id=company.id,employment_contract_id=contract.id,as_of=leave.start_date)
        assert balances[0]['from_2024']<0
        await cancel_leave_request(db,company_id=company.id,leave_request_id=leave.id,cancelled_by=user.id)
        balances=await leave_balances(db,company_id=company.id,employment_contract_id=contract.id,as_of=date(2099,1,1))
        assert balances[0]['from_2024']==D(saved[0].advance_basis['accrued_balance'])
        await db.commit()


@pytest.mark.parametrize('start,days,reason,error',[
    (date(2026,6,1),24,'six_months','Six months'),
    (date(2026,7,1),25,'six_months','exceeds'),
    (date(2026,6,1),24,'disability','dated disability'),
    (date(2026,6,1),24,'subsequent_year','first working year'),
])
async def test_advance_rejects_unproven_rights_and_excess(employee_engine,start,days,reason,error):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,account,leave=await setup(db,start,days)
        with pytest.raises(LeaveBalanceError,match=error):
            await allocate_leave_days(db,company_id=company.id,leave_request_id=leave.id,request_key='invalid',
                source_reference='Leave order',actor_id=user.id,allocations=[dict(account_id=account.id,
                    bucket='from_2024',days=D(days),advance_authorization=dict(reason=reason,
                        document_date=start,source_reference='Documented request'))])


async def test_documented_contract_exception_allows_early_full_leave(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,account,leave=await setup(db,date(2026,3,1))
        rows=await allocate_leave_days(db,company_id=company.id,leave_request_id=leave.id,request_key='early',
            source_reference='Leave order',actor_id=user.id,allocations=[dict(account_id=account.id,bucket='from_2024',
                days=D(24),advance_authorization=dict(reason='statutory_or_contractual_exception',
                    document_date=date(2026,2,28),source_reference='Verified employment agreement',
                    exception_clause='Employment agreement clause 8, early annual leave'))])
        assert rows[0].advance_basis['authorization']['exception_clause']
        assert D(rows[0].advance_basis['borrowed_days'])>20


async def test_future_accrual_settles_advance_without_second_grant(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,account,leave=await setup(db,date(2026,7,1))
        await allocate_leave_days(db,company_id=company.id,leave_request_id=leave.id,request_key='full-year',
            source_reference='Annual schedule',actor_id=user.id,allocations=[dict(account_id=account.id,
                bucket='from_2024',days=D(24),advance_authorization=dict(reason='six_months',
                    document_date=date(2026,6,30),source_reference='Six-month entitlement verified'))])
        await accrue_leave_days(db,company_id=company.id,account_id=account.id,actor_id=user.id,
            data=LeaveAccrualCreate(through=date(2026,12,31),source_reference='Completed service year',request_key='year-end'))
        balances=await leave_balances(db,company_id=company.id,employment_contract_id=contract.id,as_of=date(2026,12,31))
        assert balances[0]['from_2024']==0


async def test_disability_exception_preserves_actual_entitlement_evidence(employee_engine):
    from app.models.payroll_leave_balance import PayrollLeaveAccount
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract=await _seed_identity(db)
        contract.start_date=date(2026,1,1)
        await db.flush()
        account=await create_leave_account(db,company_id=company.id,actor_id=user.id,
            data=LeaveAccountCreate(employment_contract_id=contract.id,leave_kind='annual',entitlement_code='basic',
                period_start=date(2026,1,1),period_end=date(2026,12,31),
                segments=[dict(start=date(2026,1,1),end=date(2026,12,31),annual_days=30,
                               disability_group='II',eligibility_reference='Dated disability decision')],
                source_reference='Disability entitlement',request_key='disability'))
        await accrue_leave_days(db,company_id=company.id,account_id=account.id,actor_id=user.id,
            data=LeaveAccrualCreate(through=date(2026,2,28),source_reference='Confirmed service',request_key='february'))
        leave=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type='annual',
            start_date=date(2026,3,1),end_date=date(2026,3,30),status='pending',requested_by=user.id)
        db.add(leave);await db.flush()
        rows=await allocate_leave_days(db,company_id=company.id,leave_request_id=leave.id,request_key='disability-advance',
            source_reference='Annual leave order',actor_id=user.id,allocations=[dict(account_id=account.id,
                bucket='from_2024',days=D(30),advance_authorization=dict(reason='disability',
                    document_date=date(2026,2,28),source_reference='Reviewed disability exception'))])
        assert rows[0].advance_basis['eligibility']['disability_account_id']==account.id
        assert rows[0].advance_basis['eligibility']['disability_segment']['disability_group']=='II'


@pytest.mark.parametrize('kind,main_end,error',[
    ('external_secondary',date(2026,3,24),None),
    ('internal_secondary',date(2026,3,24),None),
    ('primary',date(2026,3,24),'secondary employment'),
    ('external_secondary',date(2026,3,23),'documented primary leave'),
])
async def test_secondary_advance_requires_matching_primary_leave(employee_engine,kind,main_end,error):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,account,leave=await setup(db,date(2026,3,1))
        contract.employment_kind=kind
        await db.flush()
        args=dict(company_id=company.id,leave_request_id=leave.id,request_key='secondary',
            source_reference='Simultaneous annual leave order',actor_id=user.id,
            allocations=[dict(account_id=account.id,bucket='from_2024',days=D(24),
                advance_authorization=dict(reason='secondary_main_leave',document_date=date(2026,2,28),
                    source_reference='Primary employer leave certificate',
                    main_leave_start=date(2026,3,1),main_leave_end=main_end))])
        if error:
            with pytest.raises(LeaveBalanceError,match=error):
                await allocate_leave_days(db,**args)
        else:
            rows=await allocate_leave_days(db,**args)
            assert rows[0].advance_basis['eligibility']['employment_kind']==kind
