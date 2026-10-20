import os
from datetime import date, datetime, timezone
from decimal import Decimal as D
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_leave_advance import setup
from test_payroll_regulatory_reporting_postgresql import _payroll_input_values
from app.models.payroll import PayrollPeriod, PayrollInput
from app.schemas.employment_event import EmploymentEventCreate
from app.schemas.employment_termination_basis import EmploymentTerminationBasis, EXEMPT_GROUNDS
from app.schemas.payroll_leave_balance import LeaveAccrualCreate
from app.schemas.payroll_leave_compensation import LeaveCompensationCreate, LeaveCompensationReverse
from app.services.employment_event_service import create_employment_event
from app.services.payroll_leave_balance_service import allocate_leave_days, accrue_leave_days, leave_balances, LeaveBalanceError
from app.services.payroll_leave_compensation_service import create_leave_compensation, reverse_leave_compensation


@pytest.mark.parametrize('ground,provision', EXEMPT_GROUNDS.items())
def test_exemption_requires_matching_statutory_provision(ground,provision):
    values=dict(ground=ground,labour_code_provision=provision,source_reference='Verified termination documents')
    assert EmploymentTerminationBasis(**values).ground==ground
    with pytest.raises(ValidationError,match='does not match'):
        EmploymentTerminationBasis(**dict(values,labour_code_provision='36.1'))


def test_nontermination_cannot_carry_exemption_evidence():
    with pytest.raises(ValidationError,match='only to termination'):
        EmploymentEventCreate(event_type='hire',effective_date=date(2026,1,1),order_date=date(2026,1,1),
            order_number='HIRE',reason='Hire',request_key='hire',termination_basis=dict(
                ground='redundancy',labour_code_provision='40.1',source_reference='Order'))


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Isolated PostgreSQL')
@pytest.mark.parametrize('ground', ['redundancy','retirement',None,'other'])
@pytest.mark.parametrize('prior_rights',[False,True])
async def test_termination_exemption_preserves_history_and_is_reversible(employee_engine,ground,prior_rights):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,account,leave=await setup(db,date(2026,7,1))
        await allocate_leave_days(db,company_id=company.id,leave_request_id=leave.id,request_key='advance',
            source_reference='Annual leave order',actor_id=user.id,allocations=[dict(account_id=account.id,
                bucket='from_2024',days=D(24),advance_authorization=dict(reason='six_months',
                    document_date=date(2026,6,30),source_reference='Approved schedule'))])
        leave.status='approved';leave.approved_by=user.id;leave.approved_at=datetime.now(timezone.utc)
        await db.flush()
        basis=dict(termination_basis=dict(ground=ground,
            labour_code_provision=EXEMPT_GROUNDS.get(ground,'36.1'),source_reference='Reviewed termination evidence')) if ground else {}
        event_data=EmploymentEventCreate(event_type='termination',effective_date=date(2026,9,30),
            order_number='TERM',order_date=date(2026,9,30),reason='Documented termination',request_key='terminate',**basis)
        event=await create_employment_event(db,company_id=company.id,contract_id=contract.id,
            data=event_data,created_by=user.id)
        assert (await create_employment_event(db,company_id=company.id,contract_id=contract.id,
            data=event_data,created_by=user.id)).id==event.id
        await accrue_leave_days(db,company_id=company.id,account_id=account.id,actor_id=user.id,
            data=LeaveAccrualCreate(through=date(2026,9,30),source_reference='Final eligible service',request_key='final-accrual'))
        period=PayrollPeriod(company_id=company.id,year=2026,month=9,start_date=date(2026,9,1),
            end_date=date(2026,9,30),status='draft',created_by=user.id)
        db.add(period);await db.flush()
        payroll_input=PayrollInput(**_payroll_input_values(company_id=company.id,period_id=period.id,
            contract_id=contract.id,actor_id=user.id))
        db.add(payroll_input);await db.flush()
        if prior_rights:
            from calendar import monthrange
            from app.models.payroll_opening import PayrollEarningsHistory
            from app.schemas.payroll_leave_balance import LeaveAccountCreate
            from app.services.payroll_leave_balance_service import create_leave_account
            for i in range(8):
                month=date(2026,i+1,1);days=monthrange(2026,i+1)[1]
                db.add(PayrollEarningsHistory(company_id=company.id,employment_contract_id=contract.id,
                    month=month,cutover_date=date(2026,9,1),gross_amount=D(days)*100,
                    vacation_earnings=D(days)*100,sick_earnings=D(days)*100,vacation_days=days,sick_days=days,
                    source_reference='Confirmed imported earnings',request_fingerprint=str(i),created_by=user.id))
            # Annual plus a separately documented additional right must not be netted.
            from datetime import timedelta
            planned=[date(2026,1,1)+timedelta(days=i) for i in range(365)]
            extra=await create_leave_account(db,company_id=company.id,actor_id=user.id,
                data=LeaveAccountCreate(employment_contract_id=contract.id,leave_kind='additional',
                    entitlement_code='documented-special-work',period_start=date(2026,1,1),period_end=date(2026,12,31),
                    segments=[dict(start=date(2026,1,1),end=date(2026,12,31),annual_days=7)],
                    planned_work_dates=planned,source_reference='Verified additional entitlement',request_key='additional'))
            await accrue_leave_days(db,company_id=company.id,account_id=extra.id,actor_id=user.id,
                data=LeaveAccrualCreate(through=date(2026,9,30),source_reference='Verified qualifying service',
                    qualifying_dates=[d for d in planned if d<=date(2026,9,30)],request_key='additional-accrual'))
        data=LeaveCompensationCreate(termination_event_id=event.id,payroll_input_id=payroll_input.id,
            request_key='settlement',entitlement_review_reference='All leave rights reviewed')
        if ground not in EXEMPT_GROUNDS:
            with pytest.raises(LeaveBalanceError,match='recovery or exemption'):
                await create_leave_compensation(db,company_id=company.id,data=data,actor_id=user.id)
            return
        before=await leave_balances(db,company_id=company.id,employment_contract_id=contract.id,as_of=date(2026,9,30))
        assert before[0]['from_2024']<0
        result=await create_leave_compensation(db,company_id=company.id,data=data,actor_id=user.id)
        assert result.gross_amount==(D(500) if prior_rights else D(0))
        assert result.recovery_exemption['termination_basis']['ground']==ground
        assert D(result.recovery_exemption['balances'][0]['from_2024'])==before[0]['from_2024']
        assert (await create_leave_compensation(db,company_id=company.id,data=data,actor_id=user.id)).id==result.id
        remaining=await leave_balances(db,company_id=company.id,employment_contract_id=contract.id,as_of=date(2026,9,30))
        assert remaining[0]==before[0]
        if prior_rights:
            assert remaining[1]['from_2024']==0
        with pytest.raises(LeaveBalanceError,match='before changing leave rights'):
            await accrue_leave_days(db,company_id=company.id,account_id=account.id,actor_id=user.id,
                data=LeaveAccrualCreate(through=date(2026,9,30),source_reference='Changed service basis',request_key='late-accrual'))
        with pytest.raises(LeaveBalanceError,match='before changing leave rights'):
            await allocate_leave_days(db,company_id=company.id,leave_request_id=leave.id,request_key='late-use',
                source_reference='Changed allocation',actor_id=user.id,
                allocations=[dict(account_id=account.id,bucket='from_2024',days=D(24))])
        from app.schemas.payroll_leave_balance import LeaveAccountCreate
        from app.services.payroll_leave_balance_service import create_leave_account
        with pytest.raises(LeaveBalanceError,match='before changing leave rights'):
            await create_leave_account(db,company_id=company.id,actor_id=user.id,
                data=LeaveAccountCreate(employment_contract_id=contract.id,leave_kind='annual',entitlement_code='basic',
                    period_start=date(2027,1,1),period_end=date(2027,12,31),
                    segments=[dict(start=date(2027,1,1),end=date(2027,12,31),annual_days=24)],
                    source_reference='Changed rights',request_key='late-account'))
        # Replaying the original source request remains safe after settlement.
        await accrue_leave_days(db,company_id=company.id,account_id=account.id,actor_id=user.id,
            data=LeaveAccrualCreate(through=date(2026,9,30),source_reference='Final eligible service',request_key='final-accrual'))
        from app.schemas.employment_event import EmploymentEventRead
        assert EmploymentEventRead.model_validate(event).termination_basis.ground==ground
        from app.services.leave_request_service import cancel_leave_request, LeaveRequestLifecycleError
        with pytest.raises(LeaveRequestLifecycleError,match='dependent termination compensation'):
            await cancel_leave_request(db,company_id=company.id,leave_request_id=leave.id,cancelled_by=user.id)
        await db.refresh(leave)
        assert leave.status=='approved'
        await db.commit()
        await reverse_leave_compensation(db,company_id=company.id,compensation_id=result.id,actor_id=user.id,
            data=LeaveCompensationReverse(effective_date=date(2026,9,30),source_reference='Correct termination',request_key='reverse'))
        replacement=await create_leave_compensation(db,company_id=company.id,
            data=data.model_copy(update={'request_key':'replacement'}),actor_id=user.id)
        assert replacement.recovery_exemption==result.recovery_exemption
        await db.commit()


@pytest.mark.parametrize('ground',['education','retirement'])
def test_fixed_term_employee_may_use_article_39(ground):
    assert EmploymentTerminationBasis(ground=ground,labour_code_provision='39',
        source_reference='Fixed-term termination evidence').labour_code_provision=='39'
