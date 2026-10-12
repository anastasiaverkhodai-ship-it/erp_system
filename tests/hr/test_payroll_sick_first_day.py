import os
from datetime import date, datetime, UTC, timedelta
from decimal import Decimal as D
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from app.models.user import User
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.leave_request import LeaveRequest
from app.schemas.payroll_insurance_evidence import InsuranceEvidenceCreate
from app.services.payroll_insurance_evidence_service import create_insurance_evidence,review_insurance_evidence
from app.services.payroll_sick_history_service import calculate_sick_from_history
from app.services.payroll_sick_leave_service import PayrollSickLeaveDerivationError,list_sick_leave_calculation_sources
from app.services.payroll_sick_allocation import sick_period_amount,FIRST_DAY_VERSION
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def setup_first_day(db, *, salary=D('30440'),recent=D(12),hire=date(2026,9,28)):
    company,user,employee,contract=await _seed_identity(db)
    contract.start_date=hire
    contract.employment_kind='primary'
    contract.work_arrangement='full_time'
    rate=EmployeeSalaryRate(company_id=company.id,employment_contract_id=contract.id,
        rate_type='monthly',amount=salary,currency_code='UAH',effective_from=hire,created_by=user.id)
    leave=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,
        leave_type='sick',start_date=hire,end_date=hire+timedelta(days=6),status='approved',
        requested_by=user.id,approved_by=user.id,approved_at=datetime.now(UTC))
    reviewer=User(email=f'first-day-reviewer-{uuid4().hex}@example.test',password_hash='test-only',
        first_name='Insurance',last_name='Reviewer',is_active=True)
    db.add_all([rate,leave,reviewer]);await db.flush()
    evidence=await create_insurance_evidence(db,company_id=company.id,created_by=user.id,
        data=InsuranceEvidenceCreate(employee_id=employee.id,case_start=hire,total_service_months=120,
            recent_service_months=recent,issued_on=hire,document_reference='Reviewed service extract',request_key='first-day'))
    await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,reviewed_by=reviewer.id,approved=True)
    return company,user,contract,rate,leave,evidence


@pytest.mark.parametrize('recent,daily',[(D(12),D('1000')),(D(5),D('284.07'))])
async def test_first_day_salary_preserves_basis_and_dated_financing(employee_engine,recent,daily):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,rate,leave,evidence=await setup_first_day(db,recent=recent)
        args=dict(company_id=company.id,leave_request_id=leave.id,insurance_evidence_id=evidence.id,calculated_by=user.id)
        row=await calculate_sick_from_history(db,**args)
        assert row.rule_version==FIRST_DAY_VERSION
        assert row.reference_period_start is None and row.reference_period_end is None
        assert row.eligible_earnings==D('30440') and row.eligible_days==D('30.44')
        assert row.daily_benefit_amount==daily
        assert row.employer_amount==daily*5 and row.insurer_amount==daily*2
        assert sick_period_amount(row,leave,date(2026,9,1),date(2026,9,30))==(3,daily*3)
        assert sick_period_amount(row,leave,date(2026,10,1),date(2026,10,31))==(4,daily*4)
        source=next(s for s in await list_sick_leave_calculation_sources(db,company_id=company.id,
            sick_leave_calculation_id=row.id) if s.source_type=='replacement_salary_rate')
        assert source.source_id==rate.id and source.earnings_amount==D('30440')
        assert (await calculate_sick_from_history(db,**args)).id==row.id
        rate.amount=D('60000');await db.flush()
        continuation=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,
            leave_type='sick',start_date=date(2026,10,5),end_date=date(2026,10,9),status='approved',
            requested_by=user.id,approved_by=user.id,approved_at=datetime.now(UTC))
        db.add(continuation);await db.flush()
        continued=await calculate_sick_from_history(db,**dict(args,leave_request_id=continuation.id))
        assert continued.daily_benefit_amount==daily
        assert continued.employer_days==0 and continued.insurer_amount==daily*5
        assert continued.eligible_earnings==D('30440')


async def test_first_day_month_boundary_financing(employee_engine):
    from test_payroll_average_classification import month
    from app.models.payroll import PayrollPeriod, PayrollCalculationLine
    from app.services.payroll_sick_accounting_service import sick_funding_for_calculation
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,rate,leave,evidence=await setup_first_day(db)
        sick=await calculate_sick_from_history(db,company_id=company.id,leave_request_id=leave.id,
            insurance_evidence_id=evidence.id,calculated_by=user.id)
        _,calculation=await month(db,company,user,contract,4000)
        period=await db.get(PayrollPeriod,calculation.payroll_period_id)
        period.month=10;period.start_date=date(2026,10,1);period.end_date=date(2026,10,31)
        line=PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calculation.id,
            line_no=2,line_type='sick_pay',quantity=4,rate=1000,amount=4000,currency_code='UAH',
            source_sick_leave_calculation_id=sick.id)
        db.add(line);await db.flush()
        args=dict(company_id=company.id,calculation=calculation,period=period)
        assert await sick_funding_for_calculation(db,**args)==(D(2000),D(2000))
        line.amount=3999;await db.flush()
        with pytest.raises(ValueError,match='dated funding'):
            await sick_funding_for_calculation(db,**args)


async def test_first_day_requires_supported_effective_rate(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,rate,leave,evidence=await setup_first_day(db)
        args=dict(company_id=company.id,leave_request_id=leave.id,insurance_evidence_id=evidence.id,calculated_by=user.id)
        rate.effective_from=leave.start_date+timedelta(days=1);await db.flush()
        with pytest.raises(PayrollSickLeaveDerivationError,match='one effective monthly'):
            await calculate_sick_from_history(db,**args)
        rate.effective_from=leave.start_date;rate.rate_type='hourly';await db.flush()
        with pytest.raises(PayrollSickLeaveDerivationError,match='one effective monthly'):
            await calculate_sick_from_history(db,**args)
        rate.rate_type='monthly';contract.work_arrangement='part_time';await db.flush()
        rate.amount=D('15220');await db.flush()
        row=await calculate_sick_from_history(db,**args)
        assert row.daily_benefit_amount==D('500')
        assert row.eligible_earnings==D('15220')


async def test_january_unverified_high_income_edge_is_not_guessed(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,rate,leave,evidence=await setup_first_day(db,salary=D('200000'),hire=date(2026,1,1))
        with pytest.raises(PayrollSickLeaveDerivationError,match='January'):
            await calculate_sick_from_history(db,company_id=company.id,leave_request_id=leave.id,
                insurance_evidence_id=evidence.id,calculated_by=user.id)


async def test_first_day_http_exposes_absent_history_and_salary_source(employee_engine):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from httpx import AsyncClient,ASGITransport
    from app.api.v1.payroll_leave_pay import router
    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.models.role import Role
    from app.models.permission import Permission
    from app.models.rbac import role_permissions
    from app.models.user_company_role import UserCompanyRole
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,rate,leave,evidence=await setup_first_day(db)
        role=Role(name='first-day-sick-user')
        permissions=[Permission(name=x) for x in ('employees.read','employees.manage')]
        db.add_all([role,*permissions]);await db.flush()
        await db.execute(role_permissions.insert(),[dict(role_id=role.id,permission_id=p.id) for p in permissions])
        db.add(UserCompanyRole(user_id=user.id,company_id=company.id,role_id=role.id))
        company_id,user_id,leave_id,evidence_id=company.id,user.id,leave.id,evidence.id
        await db.commit()
        api=FastAPI();api.include_router(router)
        async def session(): yield db
        api.dependency_overrides[get_db]=session
        api.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=user_id)
        prefix=f'/companies/{company_id}/leave-requests/{leave_id}/sick-calculation'
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            response=await client.post(prefix+'/from-history',json={'insurance_evidence_id':evidence_id})
            assert response.status_code==200,response.text
            saved=response.json()
            assert saved['reference_period_start'] is None and saved['reference_period_end'] is None
            assert saved['insurance_assessment']['basis_method']=='first_day_monthly_salary'
            assert 'replacement_salary_rate' in {s['source_type'] for s in saved['sources']}
            assert (await client.get(prefix)).json()==saved
            assert (await client.post(prefix+'/from-history',json={'insurance_evidence_id':evidence_id})).json()==saved


async def test_first_day_migration_preserves_history(employee_engine):
    import importlib.util
    from pathlib import Path
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.autogenerate import compare_metadata
    from app.core.database import Base
    spec=importlib.util.spec_from_file_location('first_day_migration',Path('alembic/versions/13d4a7b8c019_first_day_sick_salary.py'))
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    async with employee_engine.begin() as connection:
        def roundtrip(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade();migration.upgrade()
            assert compare_metadata(MigrationContext.configure(sync),Base.metadata)==[]
        await connection.run_sync(roundtrip)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,rate,leave,evidence=await setup_first_day(db)
        await calculate_sick_from_history(db,company_id=company.id,leave_request_id=leave.id,
            insurance_evidence_id=evidence.id,calculated_by=user.id)
        def downgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
        with pytest.raises(DBAPIError,match='history must be preserved'):
            async with db.begin_nested():
                await (await db.connection()).run_sync(downgrade)
