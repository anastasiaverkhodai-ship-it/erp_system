import os
from datetime import date, datetime, UTC
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from app.api.deps import get_current_user
from app.api.v1.payroll_tax_policy import router
from app.core.database import get_db
from app.models.permission import Permission
from app.models.role import Role
from app.models.rbac import role_permissions
from app.models.user_company_role import UserCompanyRole
from app.models.payroll import PayrollPeriod, PayrollInput, PayrollCalculation
from app.models.payroll_statutory import PayrollStatutoryResult

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def test_tax_policy_api_dates_entitlement_and_history(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract=await _seed_identity(db)
        ids=(company.id,user.id,employee.id,contract.id)
        role=Role(name='tax-policy-manager');perms=[Permission(name='employees.read'),Permission(name='employees.manage')]
        db.add_all([role,*perms]);await db.flush()
        await db.execute(role_permissions.insert(),[dict(role_id=role.id,permission_id=p.id) for p in perms])
        db.add(UserCompanyRole(company_id=ids[0],user_id=ids[1],role_id=role.id));await db.commit()
        api=FastAPI();api.include_router(router)
        async def session():yield db
        api.dependency_overrides[get_db]=session
        api.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=ids[1])
        prefix=f'/companies/{ids[0]}'
        profile=dict(employment_contract_id=ids[3],category='benefit_eligible',benefit_code='Application-001',
            benefit_amount_override='1600',benefit_income_limit_override='4000',effective_from='2026-01-01')
        rule=dict(component='personal_income_tax',tax_profile_category='benefit_eligible',base_mode='gross_after_benefit',
            benefit_amount='1000',benefit_income_limit='3000',rule_code='TEST',rule_version='1',effective_from='2026-01-01')
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            missing=dict(profile);missing.pop('benefit_income_limit_override')
            assert (await client.post(prefix+'/payroll-tax-profiles',json=missing)).status_code==422
            first=await client.post(prefix+'/payroll-tax-profiles',json=profile)
            assert first.status_code==201,first.text
            assert (await client.post(prefix+'/payroll-tax-profiles',json=profile)).json()==first.json()
            assert (await client.post(prefix+'/payroll-tax-profiles',json=dict(profile,benefit_code='different'))).status_code==409
            created=await client.post(prefix+'/payroll-statutory-base-rules',json=rule)
            assert created.status_code==201,created.text
            assert (await client.post(prefix+'/payroll-statutory-base-rules',json=rule)).json()==created.json()
            assert (await client.get(prefix+'/payroll-statutory-base-rules')).json()==[created.json()]
            assert (await client.post(prefix+'/payroll-statutory-base-rules',json=dict(rule,component='military_levy'))).status_code==422
            assert (await client.get('/companies/2147483647/payroll-tax-profiles')).status_code==403
            period=PayrollPeriod(company_id=ids[0],year=2026,month=9,start_date=date(2026,9,1),end_date=date(2026,9,30),
                status='finalized',created_by=ids[1],finalized_by=ids[1],finalized_at=datetime.now(UTC))
            db.add(period);await db.flush()
            source=PayrollInput(company_id=ids[0],payroll_period_id=period.id,employment_contract_id=ids[3],created_by=ids[1])
            db.add(source);await db.flush()
            calc=PayrollCalculation(company_id=ids[0],payroll_period_id=period.id,payroll_input_id=source.id,
                employment_contract_id=ids[3],gross_amount=1000,currency_code='UAH',calculated_by=ids[1])
            db.add(calc);await db.flush()
            db.add(PayrollStatutoryResult(company_id=ids[0],payroll_calculation_id=calc.id,currency_code='UAH',
                gross_amount=1000,employee_withholding_amount=230,employer_contribution_amount=220,net_amount=770,calculated_by=ids[1]))
            await db.commit()
            new_rule=dict(component='military_levy',base_mode='gross',rule_code='LATE',rule_version='1',effective_from='2026-09-01')
            blocked=await client.post(prefix+'/payroll-statutory-base-rules',json=new_rule)
            assert blocked.status_code==409 and 'saved employee month' in blocked.text
            for route, response, payload in [
                ('payroll-tax-profiles', first, profile),
                ('payroll-statutory-base-rules', created, rule),
            ]:
                end_url = prefix + f'/{route}/{response.json()["id"]}/end'
                # Removing September's policy would rewrite an already saved month.
                rejected = await client.post(end_url, json={'effective_to': '2026-08-31'})
                assert rejected.status_code == 409 and 'saved employee month' in rejected.text
                ended = await client.post(end_url, json={'effective_to': '2026-09-30'})
                assert ended.status_code == 200, ended.text
                assert ended.json()['effective_to'] == '2026-09-30'
                assert (await client.post(end_url, json={'effective_to': '2026-09-30'})).json() == ended.json()
                replacement = dict(payload, effective_from='2026-10-01')
                new = await client.post(prefix + '/' + route, json=replacement)
                assert new.status_code == 201 and new.json()['id'] != response.json()['id'], new.text
                # A former policy cannot be extended into the replacement version.
                assert (await client.post(end_url, json={'effective_to': '2026-10-31'})).status_code == 409
