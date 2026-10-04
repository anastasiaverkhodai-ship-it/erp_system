from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.deps import get_current_user
from app.api.v1 import payroll
from app.core.database import get_db
from app.schemas.payroll_register import PayrollRegister


@pytest.mark.asyncio
@pytest.mark.parametrize('allowed', [False, True])
async def test_register_requires_company_employee_read_permission(allowed):
    api = FastAPI()
    api.include_router(payroll.router)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: 1 if allowed else None)
    api.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    async def session():
        yield db
    api.dependency_overrides[get_db] = session
    report = PayrollRegister(company_id=1, payroll_period_id=2, period_status='draft',
                             complete=False, rows=[])
    with patch.object(payroll, 'get_payroll_register', AsyncMock(return_value=report)) as build:
        async with AsyncClient(transport=ASGITransport(app=api), base_url='http://test') as client:
            response = await client.get('/companies/1/payroll-periods/2/register')
        assert response.status_code == (200 if allowed else 403), response.text
        if allowed:
            build.assert_awaited_once_with(db, company_id=1, payroll_period_id=2)
            assert response.json()['complete'] is False
        else:
            build.assert_not_awaited()
        db.commit.assert_not_awaited()
