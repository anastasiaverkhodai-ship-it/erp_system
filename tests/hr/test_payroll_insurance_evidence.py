import os
from datetime import date, datetime, UTC
from decimal import Decimal as D
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_average_history import seed
from app.models.user import User
from app.models.leave_request import LeaveRequest
from app.schemas.payroll_insurance_evidence import InsuranceEvidenceCreate
from app.services.payroll_insurance_evidence_service import (
    create_insurance_evidence, review_insurance_evidence, get_insurance_evidence,
    PayrollInsuranceEvidenceError, PayrollInsuranceEvidenceNotFoundError,
)
from app.services.payroll_sick_history_service import calculate_sick_from_history, service_percentage
from app.services.payroll_sick_leave_service import PayrollSickLeaveConflictError
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def insurance_setup(db,recent=D(12),total=120):
    company,user,contract,leave,_=await seed(db)
    leave.leave_type='sick'
    reviewer=User(email=f'insurance-reviewer-{uuid4().hex}@example.test',password_hash='test-only',
        first_name='Insurance',last_name='Reviewer',is_active=True)
    db.add(reviewer);await db.flush()
    data=InsuranceEvidenceCreate(employee_id=contract.employee_id,case_start=leave.start_date,
        total_service_months=total,recent_service_months=recent,issued_on=date(2026,9,28),
        document_reference='Verified insurance-service statement',request_key='case-evidence')
    evidence=await create_insurance_evidence(db,company_id=company.id,data=data,created_by=user.id)
    return company,user,contract,leave,reviewer,data,evidence


async def test_evidence_review_retry_scope_and_automatic_history(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,reviewer,data,evidence=await insurance_setup(db)
        args=dict(company_id=company.id,leave_request_id=leave.id,insurance_evidence_id=evidence.id,calculated_by=user.id)
        assert (await create_insurance_evidence(db,company_id=company.id,data=data,created_by=user.id)).id==evidence.id
        with pytest.raises(PayrollInsuranceEvidenceError,match='Verified'):
            await calculate_sick_from_history(db,**args)
        with pytest.raises(PayrollInsuranceEvidenceError,match='independent reviewer'):
            await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,reviewed_by=user.id,approved=True)
        await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,reviewed_by=reviewer.id,approved=True)
        with pytest.raises(PayrollInsuranceEvidenceError,match='already been reviewed'):
            await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,reviewed_by=reviewer.id,approved=False)
        with pytest.raises(PayrollInsuranceEvidenceNotFoundError):
            await get_insurance_evidence(db,company_id=company.id+100000,evidence_id=evidence.id)
        row=await calculate_sick_from_history(db,**args)
        assert row.benefit_percent==100 and row.daily_benefit_amount==80
        assert row.employer_days==5 and row.insurer_days==2
        assert row.sick_pay_amount==560
        assert row.insurance_assessment['reviewed_by']==reviewer.id
        assert row.insurance_assessment['recent_service_months']=='12.000'
        assert (await calculate_sick_from_history(db,**args)).id==row.id


async def test_continuation_reuses_original_basis_without_second_employer_five_days(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,reviewer,_,evidence=await insurance_setup(db)
        await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,reviewed_by=reviewer.id,approved=True)
        first=await calculate_sick_from_history(db,company_id=company.id,leave_request_id=leave.id,
            insurance_evidence_id=evidence.id,calculated_by=user.id)
        next_leave=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type='sick',
            start_date=date(2026,10,5),end_date=date(2026,10,9),status='approved',requested_by=user.id,
            approved_by=user.id,approved_at=datetime.now(UTC))
        db.add(next_leave);await db.flush()
        continued=await calculate_sick_from_history(db,company_id=company.id,leave_request_id=next_leave.id,
            insurance_evidence_id=evidence.id,calculated_by=user.id)
        assert continued.employer_days==0 and continued.insurer_days==5
        assert continued.sick_pay_amount==400
        assert continued.reference_period_end==date(2026,8,31)
        assert continued.insurance_assessment['initial_calculation_id']==first.id
        gap=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type='sick',
            start_date=date(2026,10,11),end_date=date(2026,10,12),status='approved',requested_by=user.id,
            approved_by=user.id,approved_at=datetime.now(UTC))
        db.add(gap);await db.flush()
        with pytest.raises(PayrollSickLeaveConflictError,match='gap'):
            await calculate_sick_from_history(db,company_id=company.id,leave_request_id=gap.id,
                insurance_evidence_id=evidence.id,calculated_by=user.id)


async def test_recent_service_drives_limit_independently_of_lifetime_service(employee_engine):
    from app.models.payroll_opening import PayrollEarningsHistory
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,reviewer,_,evidence=await insurance_setup(db,recent=D(5))
        for history in (await db.scalars(select(PayrollEarningsHistory).where(
            PayrollEarningsHistory.employment_contract_id==contract.id))).all():
            history.gross_amount=history.sick_days*1000
            history.sick_earnings=history.sick_days*1000
        await db.flush()
        await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,reviewed_by=reviewer.id,approved=True)
        row=await calculate_sick_from_history(db,company_id=company.id,leave_request_id=leave.id,
            insurance_evidence_id=evidence.id,calculated_by=user.id)
        assert row.benefit_percent==100
        assert row.limited_service_rule_applied
        assert row.daily_benefit_amount==D('284.07')
        assert row.sick_pay_amount==D('1988.49')


async def test_insurance_and_sick_http_permissions_and_review(employee_engine):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from httpx import AsyncClient,ASGITransport
    from app.api.v1.payroll_insurance_evidence import router as evidence_router
    from app.api.v1.payroll_leave_pay import router as leave_router
    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.models.role import Role
    from app.models.permission import Permission
    from app.models.rbac import role_permissions
    from app.models.user_company_role import UserCompanyRole
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,_,leave,reviewer,data,evidence=await insurance_setup(db)
        role=Role(name='insurance-payroll')
        permissions=[Permission(name='employees.read'),Permission(name='employees.manage')]
        db.add_all([role,*permissions]);await db.flush()
        await db.execute(role_permissions.insert(),[dict(role_id=role.id,permission_id=p.id) for p in permissions])
        db.add_all([UserCompanyRole(user_id=u.id,company_id=company.id,role_id=role.id) for u in (user,reviewer)])
        await db.commit()
        ids=dict(company=company.id,author=user.id,reviewer=reviewer.id,evidence=evidence.id,leave=leave.id)
        actor=SimpleNamespace(id=ids['author'])
        api=FastAPI();api.include_router(evidence_router);api.include_router(leave_router)
        async def session(): yield db
        api.dependency_overrides[get_db]=session
        api.dependency_overrides[get_current_user]=lambda:actor
        prefix=f"/companies/{ids['company']}/payroll-insurance-evidence"
        leave_url=f"/companies/{ids['company']}/leave-requests/{ids['leave']}/sick-calculation"
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            created=await client.post(prefix,json=data.model_dump(mode='json'))
            assert created.status_code==201,created.text
            assert created.json()['id']==ids['evidence']
            assert (await client.get(f'/companies/2147483647/payroll-insurance-evidence/{ids["evidence"]}')).status_code==403
            assert (await client.post(prefix+f'/{ids["evidence"]}/review',json={'approved':True})).status_code==409
            actor.id=ids['reviewer']
            approved=await client.post(prefix+f'/{ids["evidence"]}/review',json={'approved':True})
            assert approved.status_code==200,approved.text
            payload={'insurance_evidence_id':ids['evidence']}
            assert (await client.post(leave_url+'/from-history',json=dict(payload,benefit_percent=80))).status_code==422
            result=await client.post(leave_url+'/from-history',json=payload)
            assert result.status_code==200,result.text
            assert D(result.json()['sick_pay_amount'])==560
            assert len(result.json()['sources'])==13
            assert (await client.get(leave_url)).json()==result.json()
            assert (await client.post(leave_url+'/from-history',json=payload)).json()==result.json()


@pytest.mark.parametrize('kind', ['internal_secondary', 'external_secondary', None])
async def test_automatic_sick_requires_attributed_primary_basis(employee_engine, kind):
    from app.services.payroll_sick_leave_service import PayrollSickLeaveDerivationError
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company,user,contract,leave,reviewer,_,evidence=await insurance_setup(db)
        contract.employment_kind=kind
        await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,
            reviewed_by=reviewer.id,approved=True)
        with pytest.raises(PayrollSickLeaveDerivationError,match='combined attributed'):
            await calculate_sick_from_history(db,company_id=company.id,leave_request_id=leave.id,
                insurance_evidence_id=evidence.id,calculated_by=user.id)


async def test_january_sick_does_not_use_february_monthly_ceiling(employee_engine, monkeypatch):
    import app.services.payroll_sick_history_service as service
    from app.services.payroll_sick_leave_service import PayrollSickLeaveDerivationError
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company,user,contract,leave,reviewer,_,evidence=await insurance_setup(db)
        leave.start_date=date(2026,1,1);leave.end_date=date(2026,1,31)
        evidence.case_start=date(2026,1,1)
        await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,
            reviewed_by=reviewer.id,approved=True)
        async def basis(*args, **kwargs):
            return dict(eligible_earnings=D(5200)*365,eligible_days=D(365))
        monkeypatch.setattr(service,'derive_leave_average_history',basis)
        with pytest.raises(PayrollSickLeaveDerivationError,match='monthly maximum'):
            await calculate_sick_from_history(db,company_id=company.id,leave_request_id=leave.id,
                insurance_evidence_id=evidence.id,calculated_by=user.id)
