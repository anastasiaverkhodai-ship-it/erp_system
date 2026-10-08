"""Real imported months feed an attributed leave calculation through HTTP."""
import os
from calendar import monthrange
from datetime import date, datetime, UTC
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from app.api.deps import get_current_user
from app.api.v1.payroll_leave_pay import router
from app.core.database import get_db
from app.models.payroll import PayrollPeriod, PayrollInput, PayrollCalculation
from app.models.payroll_opening import PayrollEarningsHistory
from app.models.payroll_vacation import PayrollVacationCalculation
from app.models.leave_request import LeaveRequest
from app.models.permission import Permission
from app.models.role import Role
from app.models.rbac import role_permissions
from app.models.user_company_role import UserCompanyRole
from app.schemas.payroll_opening import PayrollEarningsHistoryInput
from app.services.payroll_opening_service import import_earnings_history
from app.services.payroll_average_history_service import derive_leave_average_history, PayrollAverageHistoryError

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')]


async def seed(db, *, erp_month=False, hire_date=date(2025,1,1)):
    company, user, _, contract = await _seed_identity(db)
    contract.start_date = hire_date
    leave = LeaveRequest(company_id=company.id, employment_contract_id=contract.id,
        leave_type='annual', start_date=date(2026,9,28), end_date=date(2026,10,4),
        status='approved', requested_by=user.id, approved_by=user.id, approved_at=datetime.now(UTC))
    db.add(leave)
    await db.flush()
    histories=[]
    for i in range(11 if erp_month else 12):
        month=date(2025+(8+i)//12, (8+i)%12+1, 1)
        if month < hire_date.replace(day=1):
            continue
        days=monthrange(month.year,month.month)[1]
        row=await import_earnings_history(db,company_id=company.id,contract_id=contract.id,
            data=PayrollEarningsHistoryInput(month=month,cutover_date=date(2026,8,1) if erp_month else date(2026,9,1),
                gross_amount=D(days)*100,vacation_earnings=D(days)*100,sick_earnings=D(days)*80,
                vacation_days=days,sick_days=days,source_reference='Verified payroll register '+month.isoformat()),
            created_by=user.id)
        histories.append(row)
    return company,user,contract,leave,histories


async def test_history_basis_and_vacation_http(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,histories=await seed(db)
        role=Role(name='leave-history-api')
        perms=[Permission(name='employees.read'),Permission(name='employees.manage')]
        db.add_all([role,*perms]);await db.flush()
        await db.execute(role_permissions.insert(),[dict(role_id=role.id,permission_id=p.id) for p in perms])
        db.add(UserCompanyRole(user_id=user.id,company_id=company.id,role_id=role.id))
        await db.commit()
        company_id, contract_id, actor_id = company.id, contract.id, user.id
        api=FastAPI();api.include_router(router)
        async def session(): yield db
        api.dependency_overrides[get_db]=session
        api.dependency_overrides[get_current_user]=lambda: SimpleNamespace(id=actor_id)
        url=f'/companies/{company.id}/leave-requests/{leave.id}'
        payload=dict(reference_period_start='2025-09-01',reference_period_end='2026-08-31')
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            basis=await client.get(url+'/average-history')
            assert basis.status_code==200,basis.text
            assert D(basis.json()['eligible_earnings'])==36500
            assert D(basis.json()['eligible_days'])==365
            assert D(basis.json()['average_daily_amount'])==100
            assert len(basis.json()['sources'])==12
            assert (await client.get(url+'/vacation-calculation')).status_code==404
            assert (await client.post(url+'/vacation-calculation/from-history',json=dict(payload,eligible_earnings='1'))).status_code==422
            result=await client.post(url+'/vacation-calculation/from-history',json={})
            assert result.status_code==200,result.text
            assert D(result.json()['vacation_pay_amount'])==700
            assert {s['source_id'] for s in result.json()['sources']}=={h.id for h in histories}
            assert (await client.get(url+'/vacation-calculation')).json()==result.json()
            assert (await client.post(url+'/vacation-calculation/from-history',json=payload)).json()==result.json()
            changed=await client.post(url+'/vacation-calculation/from-history',json=dict(payload,reference_period_start='2025-10-01'))
            assert changed.status_code==409
            assert (await client.get('/companies/2147483647/leave-requests/1/average-history',params=payload)).status_code==403
        from app.services.payroll_vacation_service import derive_vacation_pay_lines
        allocated=[]
        for start,end in [(date(2026,9,1),date(2026,9,30)),(date(2026,10,1),date(2026,10,31))]:
            lines=await derive_vacation_pay_lines(db,company_id=company_id,
                payroll_input=SimpleNamespace(employment_contract_id=contract_id),
                period=SimpleNamespace(start_date=start,end_date=end))
            allocated.append(sum(l['amount'] for l in lines))
        assert allocated==[D('300'),D('400')]
        assert await db.scalar(select(func.count()).select_from(PayrollVacationCalculation))==1


async def test_average_rejects_missing_ambiguous_or_unattributed_history(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,histories=await seed(db)
        args=dict(company_id=company.id,leave_request_id=leave.id,
            reference_period_start=date(2025,9,1),reference_period_end=date(2026,8,31))
        for field,value,match in [('reference_period_start',date(2025,10,1),'all applicable'),
                                 ('reference_period_end',date(2026,8,30),'complete months')]:
            with pytest.raises(PayrollAverageHistoryError,match=match):
                await derive_leave_average_history(db,**dict(args,**{field:value}))
        # Deleting a month must not lower the denominator or treat it as zero.
        first=histories[0]
        await db.delete(first);await db.flush()
        with pytest.raises(PayrollAverageHistoryError,match='2025-09-01'):
            await derive_leave_average_history(db,**args)
        from sqlalchemy.orm import make_transient
        make_transient(first);db.add(first);await db.flush()
        first.source_reference=' '
        with pytest.raises(PayrollAverageHistoryError,match='supporting reference'):
            await derive_leave_average_history(db,**args)
        first.source_reference='Verified September'
        leave.leave_type='sick';await db.flush()
        basis=await derive_leave_average_history(db,**args)
        assert basis['purpose']=='sick' and basis['average_daily_amount']==80
        period=PayrollPeriod(company_id=company.id,year=2026,month=8,start_date=date(2026,8,1),
            end_date=date(2026,8,31),status='draft',created_by=user.id)
        db.add(period);await db.flush()
        source=PayrollInput(company_id=company.id,payroll_period_id=period.id,
            employment_contract_id=contract.id,created_by=user.id)
        db.add(source);await db.flush()
        db.add(PayrollCalculation(company_id=company.id,payroll_period_id=period.id,payroll_input_id=source.id,
            employment_contract_id=contract.id,gross_amount=100,currency_code='UAH',calculated_by=user.id))
        await db.flush()
        with pytest.raises(PayrollAverageHistoryError,match='ERP earnings'):
            await derive_leave_average_history(db,**args)


async def test_vacation_combines_imported_months_with_current_erp_salary(employee_engine):
    from app.models.payroll import PayrollCalculationLine
    from app.services.payroll_average_history_service import calculate_vacation_from_history
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,histories=await seed(db,erp_month=True)
        period=PayrollPeriod(company_id=company.id,year=2026,month=8,start_date=date(2026,8,1),
            end_date=date(2026,8,31),status='finalized',created_by=user.id,
            finalized_by=user.id,finalized_at=datetime.now(UTC))
        db.add(period);await db.flush()
        source=PayrollInput(company_id=company.id,payroll_period_id=period.id,
            employment_contract_id=contract.id,created_by=user.id)
        db.add(source);await db.flush()
        calc=PayrollCalculation(company_id=company.id,payroll_period_id=period.id,payroll_input_id=source.id,
            employment_contract_id=contract.id,gross_amount=3100,currency_code='UAH',calculated_by=user.id)
        db.add(calc);await db.flush()
        line=PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calc.id,line_no=1,
            line_type='salary',quantity=1,rate=3100,amount=3100,currency_code='UAH')
        db.add(line);await db.flush()
        args=dict(company_id=company.id,leave_request_id=leave.id)
        basis=await derive_leave_average_history(db,**args)
        assert basis['eligible_earnings']==36500 and basis['eligible_days']==365
        assert basis['sources'][-1]['source_type']=='payroll_calculation'
        assert basis['sources'][-1]['source_id']==calc.id
        # A candidate revision is not current until explicitly linked.
        candidate=PayrollCalculation(company_id=company.id,payroll_period_id=period.id,payroll_input_id=source.id,
            employment_contract_id=contract.id,revision=2,gross_amount=9999,currency_code='UAH',calculated_by=user.id)
        db.add(candidate);await db.flush()
        assert (await derive_leave_average_history(db,**args))['eligible_earnings']==36500
        unpaid=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type='unpaid',
            start_date=date(2026,8,1),end_date=date(2026,8,5),status='approved',requested_by=user.id,
            approved_by=user.id,approved_at=datetime.now(UTC))
        db.add(unpaid);await db.flush()
        assert (await derive_leave_average_history(db,**args))['eligible_days']==360
        line.line_type='manual_adjustment';await db.flush()
        with pytest.raises(PayrollAverageHistoryError,match='non-salary'):
            await derive_leave_average_history(db,**args)
        line.line_type='salary';await db.flush()
        row=await calculate_vacation_from_history(db,**args,calculated_by=user.id)
        assert row.eligible_days==360 and row.vacation_pay_amount==D('709.72')


async def test_shorter_employment_and_unverified_future_calendar(employee_engine):
    from app.services.payroll_average_history_service import calculate_vacation_from_history
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,histories=await seed(db,hire_date=date(2026,1,1))
        args=dict(company_id=company.id,leave_request_id=leave.id)
        basis=await derive_leave_average_history(db,**args)
        assert basis['reference_period_start']==date(2026,1,1)
        assert len(basis['sources'])==8 and basis['eligible_days']==243
        leave.end_date=date(2026,10,31);await db.flush()
        with pytest.raises(PayrollAverageHistoryError,match='verified rule'):
            await calculate_vacation_from_history(db,**args,calculated_by=user.id)
        assert await db.scalar(select(func.count()).select_from(PayrollVacationCalculation))==0
        # Do not quietly assume the first incomplete employment month is excluded.
        contract.start_date=date(2026,1,2);await db.flush()
        with pytest.raises(PayrollAverageHistoryError):
            await derive_leave_average_history(db,**args)
