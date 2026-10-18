import os
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from app.schemas.payroll_leave_balance import LeaveAccountCreate, LeaveAccrualCreate
from app.services.payroll_leave_balance_service import create_leave_account, accrue_leave_days, leave_balances, LeaveBalanceError

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Isolated PostgreSQL')]


async def test_account_accrual_and_idempotency(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract=await _seed_identity(db)
        contract.start_date=date(2025,1,1);await db.flush()
        data=LeaveAccountCreate(employment_contract_id=contract.id,leave_kind='annual',entitlement_code='basic',
            period_start=date(2026,1,1),period_end=date(2026,12,31),
            segments=[dict(start=date(2026,1,1),end=date(2026,12,31),annual_days=24)],
            source_reference='Employment terms and service record',request_key='annual-2026')
        account=await create_leave_account(db,company_id=company.id,data=data,actor_id=user.id)
        assert (await create_leave_account(db,company_id=company.id,data=data,actor_id=user.id)).id==account.id
        accrual=LeaveAccrualCreate(through=date(2026,9,30),source_reference='Confirmed service',request_key='september')
        rows=await accrue_leave_days(db,company_id=company.id,account_id=account.id,data=accrual,actor_id=user.id)
        assert rows[0].days==(D(273)*24/365).quantize(D('.00000001'))
        assert [r.id for r in await accrue_leave_days(db,company_id=company.id,account_id=account.id,data=accrual,actor_id=user.id)]==[rows[0].id]
        with pytest.raises(LeaveBalanceError,match='different'):
            await accrue_leave_days(db,company_id=company.id,account_id=account.id,data=accrual.model_copy(update={'through':date(2026,10,1)}),actor_id=user.id)
        no_change=accrual.model_copy(update={'request_key':'same-day-new-request'})
        assert await accrue_leave_days(db,company_id=company.id,account_id=account.id,data=no_change,actor_id=user.id)==[]
        with pytest.raises(LeaveBalanceError,match='different'):
            await accrue_leave_days(db,company_id=company.id,account_id=account.id,data=no_change.model_copy(update={'through':date(2026,10,1)}),actor_id=user.id)
        balances=await leave_balances(db,company_id=company.id,employment_contract_id=contract.id,as_of=date(2026,9,30))
        assert balances[0]['from_2024']==rows[0].days
        historical=await leave_balances(db,company_id=company.id,employment_contract_id=contract.id,as_of=date(2026,9,29))
        assert historical[0]['from_2024']==0


async def test_reserved_leave_approval_and_cancellation_restore_days(employee_engine):
    from app.models.leave_request import LeaveRequest
    from app.services.leave_request_service import approve_leave_request, cancel_leave_request, LeaveRequestLifecycleError
    from app.services.payroll_leave_balance_service import allocate_leave_days
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract=await _seed_identity(db)
        contract.start_date=date(2025,1,1);await db.flush()
        account=await create_leave_account(db,company_id=company.id,actor_id=user.id,data=LeaveAccountCreate(
            employment_contract_id=contract.id,leave_kind='social',entitlement_code='parental',
            period_start=date(2026,1,1),period_end=date(2026,12,31),social_days=10,
            social_grounds=['two_children_under_15'],source_reference='Confirmed parental eligibility and non-use by other parent',request_key='social'))
        await accrue_leave_days(db,company_id=company.id,account_id=account.id,actor_id=user.id,
            data=LeaveAccrualCreate(through=date(2026,9,30),source_reference='Confirmed yearly right',request_key='social-accrual'))
        leave=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type='social',
            start_date=date(2026,10,1),end_date=date(2026,10,3),status='pending',requested_by=user.id)
        db.add(leave);await db.flush()
        with pytest.raises(LeaveRequestLifecycleError,match='Allocate'):
            await approve_leave_request(db,company_id=company.id,leave_request_id=leave.id,approved_by=user.id)
        await allocate_leave_days(db,company_id=company.id,leave_request_id=leave.id,
            allocations=[dict(account_id=account.id,bucket='from_2024',days=D(3))],
            request_key='reserve',source_reference='Leave request',actor_id=user.id)
        await approve_leave_request(db,company_id=company.id,leave_request_id=leave.id,approved_by=user.id)
        before=await leave_balances(db,company_id=company.id,employment_contract_id=contract.id,as_of=date(2026,10,1))
        assert before[0]['from_2024']==7
        await cancel_leave_request(db,company_id=company.id,leave_request_id=leave.id,cancelled_by=user.id)
        after=await leave_balances(db,company_id=company.id,employment_contract_id=contract.id,as_of=date(2099,1,1))
        assert after[0]['from_2024']==10
        with pytest.raises(LeaveBalanceError,match='Overlapping'):
            await create_leave_account(db,company_id=company.id,actor_id=user.id,data=LeaveAccountCreate(
                employment_contract_id=contract.id,leave_kind='social',entitlement_code='another-name',
                period_start=date(2026,1,1),period_end=date(2026,12,31),social_days=10,
                social_grounds=['another-ground'],source_reference='Cannot duplicate yearly cap',request_key='duplicate'))


async def test_additional_leave_prorates_against_working_calendar(employee_engine):
    from datetime import timedelta
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract=await _seed_identity(db)
        contract.start_date=date(2025,1,1);await db.flush()
        planned=[date(2026,1,1)+timedelta(days=i) for i in range(365)
                 if (date(2026,1,1)+timedelta(days=i)).weekday()<5]
        account=await create_leave_account(db,company_id=company.id,actor_id=user.id,data=LeaveAccountCreate(
            employment_contract_id=contract.id,leave_kind='additional',entitlement_code='special_work',
            period_start=date(2026,1,1),period_end=date(2026,12,31),planned_work_dates=planned,
            segments=[dict(start=date(2026,1,1),end=date(2026,12,31),annual_days=7)],
            source_reference='Collective agreement and full-year shift calendar',request_key='additional'))
        qualified=[day for day in planned if day<=date(2026,9,30)]
        data=LeaveAccrualCreate(through=date(2026,9,30),qualifying_dates=qualified,
            source_reference='Qualifying work at least half of normal day and eligible annual leave',request_key='qualify')
        rows=await accrue_leave_days(db,company_id=company.id,account_id=account.id,actor_id=user.id,data=data)
        assert rows[0].days==(D(7)*len(qualified)/len(planned)).quantize(D('.00000001'))
        assert rows[0].days>D(7)*len(qualified)/365
        with pytest.raises(LeaveBalanceError,match='working calendar'):
            await accrue_leave_days(db,company_id=company.id,account_id=account.id,actor_id=user.id,
                data=data.model_copy(update={'request_key':'invalid','qualifying_dates':qualified+[date(2026,1,3)]}))


async def test_failed_day_return_does_not_cancel_request(employee_engine, monkeypatch):
    from app.models.leave_request import LeaveRequest
    from app.services.leave_request_service import cancel_leave_request, LeaveRequestLifecycleError
    import app.services.payroll_leave_balance_service as ledger
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract=await _seed_identity(db)
        leave=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type='annual',
            start_date=date(2026,10,1),end_date=date(2026,10,3),status='pending',requested_by=user.id)
        db.add(leave);await db.flush()
        leave_id=leave.id
        async def cannot_restore(*args, **kwargs):
            raise LeaveBalanceError('Dependent compensation must be reversed')
        monkeypatch.setattr(ledger,'return_cancelled_leave_days',cannot_restore)
        with pytest.raises(LeaveRequestLifecycleError,match='Dependent'):
            await cancel_leave_request(db,company_id=company.id,leave_request_id=leave_id,cancelled_by=user.id)
        await db.refresh(leave)
        assert leave.status=='pending' and leave.cancelled_by is None
        await db.commit()
