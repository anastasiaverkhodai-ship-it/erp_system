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
        with pytest.raises(
            PayrollSickLeaveDerivationError,
            match=(
                'Complete same-company primary employment evidence required'
                if kind == 'internal_secondary'
                else (
                    'External secondary employment requires verified other-employer insurance earnings'
                )
            ),
        ):
            await calculate_sick_from_history(db,company_id=company.id,leave_request_id=leave.id,
                insurance_evidence_id=evidence.id,calculated_by=user.id)


async def test_january_sick_caps_month_and_carries_basis_into_february(employee_engine):
    from calendar import monthrange
    from types import SimpleNamespace
    from app.models.payroll_opening import PayrollEarningsHistory
    from app.services.payroll_sick_leave_service import derive_sick_pay_lines
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company,user,contract,leave,reviewer,_,evidence=await insurance_setup(db)
        leave.start_date=date(2026,1,1);leave.end_date=date(2026,1,20)
        evidence.case_start=date(2026,1,1)
        histories=(await db.scalars(select(PayrollEarningsHistory).where(
            PayrollEarningsHistory.employment_contract_id==contract.id).order_by(PayrollEarningsHistory.month))).all()
        for i,history in enumerate(histories,1):
            history.month=date(2025,i,1);history.cutover_date=date(2026,1,1)
            history.gross_amount=history.sick_earnings=D(160000)
            history.sick_days=history.vacation_days=D(monthrange(2025,i)[1])
        await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,
            reviewed_by=reviewer.id,approved=True)
        first=await calculate_sick_from_history(db,company_id=company.id,leave_request_id=leave.id,
            insurance_evidence_id=evidence.id,calculated_by=user.id)
        assert first.daily_benefit_amount==D('5256.24')
        assert first.sick_pay_amount==D('105124.80')
        second=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type='sick',
            start_date=date(2026,1,21),end_date=date(2026,2,3),status='approved',requested_by=user.id,
            approved_by=user.id,approved_at=datetime.now(UTC))
        db.add(second);await db.flush()
        continued=await calculate_sick_from_history(db,company_id=company.id,leave_request_id=second.id,
            insurance_evidence_id=evidence.id,calculated_by=user.id)
        assert continued.employer_amount==0
        assert continued.sick_pay_amount==D('70643.92')
        assert continued.insurance_assessment['monthly_maximum']=='160000.00'
        assert first.sick_pay_amount+continued.sick_pay_amount==D('175768.72')
        for month,expected in [(1,D(160000)),(2,D('15768.72'))]:
            lines=await derive_sick_pay_lines(db,company_id=company.id,
                payroll_input=SimpleNamespace(employment_contract_id=contract.id),
                period=SimpleNamespace(start_date=date(2026,month,1),end_date=date(2026,month,monthrange(2026,month)[1])))
            assert sum(x['amount'] for x in lines)==expected
        assert (await calculate_sick_from_history(db,company_id=company.id,leave_request_id=second.id,
            insurance_evidence_id=evidence.id,calculated_by=user.id)).id==continued.id


async def test_legacy_insurance_continuation_preserves_original_rounding(employee_engine):
    from app.services.payroll_sick_leave_service import calculate_sick_leave_pay
    from app.services.payroll_sick_history_service import RULE_CODE, LEGACY_RULE_VERSION
    from app.services.payroll_average_history_service import derive_leave_average_history
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,reviewer,_,evidence=await insurance_setup(db)
        await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,
            reviewed_by=reviewer.id,approved=True)
        basis=await derive_leave_average_history(db,company_id=company.id,leave_request_id=leave.id)
        first=await calculate_sick_leave_pay(db,company_id=company.id,leave_request_id=leave.id,
            benefit_case_code='ORDINARY_TEMPORARY_INCAPACITY',reference_period_start=basis['reference_period_start'],
            reference_period_end=basis['reference_period_end'],eligible_earnings=basis['eligible_earnings'],
            eligible_days=basis['eligible_days'],insurance_service_months=120,benefit_percent=D(100),
            limited_service_rule_applied=False,employer_days=D(5),insurer_days=D(2),rule_code=RULE_CODE,
            rule_version=LEGACY_RULE_VERSION,calculated_by=user.id,sources=basis['sources'])
        first.insurance_evidence_id=evidence.id
        first.insurance_assessment={'rule_version':LEGACY_RULE_VERSION}
        second=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type='sick',
            start_date=date(2026,10,5),end_date=date(2026,10,9),status='approved',requested_by=user.id,
            approved_by=user.id,approved_at=datetime.now(UTC))
        db.add(second);await db.flush()
        continued=await calculate_sick_from_history(db,company_id=company.id,leave_request_id=second.id,
            insurance_evidence_id=evidence.id,calculated_by=user.id)
        assert continued.rule_version==LEGACY_RULE_VERSION
        assert continued.sick_pay_amount==400
        assert 'daily_allocation' not in continued.insurance_assessment
        assert first.sick_pay_amount==560


async def test_new_earlier_case_cannot_reallocate_existing_later_certificate(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,reviewer,_,evidence=await insurance_setup(db)
        await review_insurance_evidence(db,company_id=company.id,evidence_id=evidence.id,
            reviewed_by=reviewer.id,approved=True)
        await calculate_sick_from_history(db,company_id=company.id,leave_request_id=leave.id,
            insurance_evidence_id=evidence.id,calculated_by=user.id)
        earlier=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type='sick',
            start_date=date(2026,9,10),end_date=date(2026,9,12),status='approved',requested_by=user.id,
            approved_by=user.id,approved_at=datetime.now(UTC))
        db.add(earlier);await db.flush()
        data=InsuranceEvidenceCreate(employee_id=contract.employee_id,case_start=earlier.start_date,
            total_service_months=120,recent_service_months=12,issued_on=date(2026,9,10),
            document_reference='Earlier case evidence',request_key='earlier-case')
        proof=await create_insurance_evidence(db,company_id=company.id,data=data,created_by=user.id)
        await review_insurance_evidence(db,company_id=company.id,evidence_id=proof.id,
            reviewed_by=reviewer.id,approved=True)
        with pytest.raises(PayrollSickLeaveConflictError,match='Later or overlapping'):
            await calculate_sick_from_history(db,company_id=company.id,leave_request_id=earlier.id,
                insurance_evidence_id=proof.id,calculated_by=user.id)
