"""Exercise HTTP -> actual service -> PostgreSQL, including unequal source IDs."""
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_employment_structure import setup, contract
from app.api.deps import get_current_user
from app.api.v1 import payroll, payroll_statutory
from app.core.database import get_db
from app.models.payroll import PayrollPeriod, PayrollInput, PayrollCalculation
from app.models.permission import Permission
from app.models.role import Role
from app.models.rbac import role_permissions
from app.models.user_company_role import UserCompanyRole

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')]


async def test_statutory_and_input_routes_use_real_service_contracts(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f = await setup(db)
        employment = await contract(db, f)
        role = Role(name='payroll-api-reader-manager')
        permissions = [Permission(name='employees.read'), Permission(name='employees.manage')]
        db.add_all([role, *permissions])
        await db.flush()
        await db.execute(role_permissions.insert(), [dict(role_id=role.id, permission_id=p.id) for p in permissions])
        db.add(UserCompanyRole(user_id=f['actor'], company_id=f['company'], role_id=role.id))
        period = PayrollPeriod(company_id=f['company'], year=2026, month=9,
            start_date=date(2026,9,1), end_date=date(2026,9,30), status='finalized', created_by=f['actor'], finalized_by=f['actor'], finalized_at=datetime.now(timezone.utc))
        db.add(period)
        await db.flush()
        source = PayrollInput(company_id=f['company'], payroll_period_id=period.id,
            employment_contract_id=employment.id, created_by=f['actor'])
        db.add(source)
        await db.flush()
        calc = PayrollCalculation(id=101, company_id=f['company'], payroll_period_id=period.id,
            payroll_input_id=source.id, employment_contract_id=employment.id,
            gross_amount=Decimal('10000'), currency_code='UAH', calculated_by=f['actor'])
        db.add(calc)
        await db.commit()
        api = FastAPI()
        api.include_router(payroll_statutory.router)
        api.include_router(payroll.router)
        async def session():
            yield db
        api.dependency_overrides[get_db] = session
        api.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=f['actor'])
        prefix = f"/companies/{f['company']}"
        async with AsyncClient(transport=ASGITransport(app=api), base_url='http://test') as client:
            for component, rate in [('personal_income_tax', '.18'), ('military_levy', '.05'), ('unified_social_contribution', '.22')]:
                response = await client.post(prefix+'/payroll-statutory-rates', json=dict(
                    component=component, rate=rate, effective_from='2026-01-01'))
                assert response.status_code == 201, response.text
            individual = await client.post(prefix+'/payroll-statutory-rates', json=dict(
                component='unified_social_contribution', rate='.0841', employee_id=f['employee'],
                source_reference='Disability evidence API-001', effective_from='2026-01-01', effective_to='2026-12-31'))
            assert individual.status_code == 201, individual.text
            for suffix in ('statutory', 'statutory/lines'):
                missing = await client.get(prefix+f'/payroll-calculations/{calc.id}/{suffix}')
                assert missing.status_code == 404, missing.text
            calculated = await client.post(prefix+f'/payroll-calculations/{calc.id}/statutory/calculate')
            assert calculated.status_code == 200, calculated.text
            data = calculated.json()
            assert Decimal(data['net_amount']) == Decimal('7700')
            assert data['id'] != calc.id
            result = await client.get(prefix+f'/payroll-calculations/{calc.id}/statutory')
            assert result.status_code == 200 and result.json()['id'] == data['id']
            lines = await client.get(prefix+f'/payroll-calculations/{calc.id}/statutory/lines')
            assert lines.status_code == 200, lines.text
            assert len(lines.json()) == 3
            usc = next(line for line in lines.json() if line['component'] == 'unified_social_contribution')
            assert Decimal(usc['amount']) == Decimal('841.00')
            assert usc['source_rate_id'] == individual.json()['id']
            assert all(line['payroll_statutory_result_id'] == data['id'] for line in lines.json())
            repeated = await client.post(prefix+f'/payroll-calculations/{calc.id}/statutory/calculate')
            assert repeated.status_code == 200 and repeated.json()['id'] == data['id']
            inputs_path=prefix+f'/payroll-periods/{period.id}/inputs'
            for query, count in [('',1), (f'?contract_id={employment.id}',1), ('?contract_id=2147483647',0)]:
                inputs = await client.get(inputs_path+query)
                assert inputs.status_code == 200, inputs.text
                assert len(inputs.json()) == count
            foreign = await client.get(f'/companies/{f["foreign_company"]}/payroll-calculations/{calc.id}/statutory') if 'foreign_company' in f else await client.get(f'/companies/2147483647/payroll-calculations/{calc.id}/statutory')
            assert foreign.status_code == 403
