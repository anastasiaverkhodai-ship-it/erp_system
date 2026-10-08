from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.api.v1 import payroll_advances
from app.core.database import get_db


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path,payload,service', [
    ('GET', '/companies/1/payroll-periods/2/employment-contracts/3/advance', None, 'get_payroll_advance'),
    ('GET', '/companies/1/payroll-periods/2/employment-contracts/3/advance-basis?advance_percentage=40', None, 'derive_payroll_advance_basis'),
    ('POST', '/companies/1/payroll-advances', dict(payroll_period_id=2, employment_contract_id=3,
        bank_account_id=4, advance_percentage='40', calculation_base_amount='10000',
        minimum_due_amount='1000', currency_code='UAH', payment_date='2026-09-20'), 'create_payroll_advance'),
    ('POST', '/companies/1/payroll-advances/3/bank-reconciliations', dict(bank_statement_line_id=4,
        matched_amount='10', currency_code='UAH'), 'reconcile_payroll_advance'),
    ('POST', '/companies/1/payroll-advances/3/bank-reconciliations/4/reverse', None, 'unmatch_payroll_advance'),
])
async def test_advance_endpoints_reject_user_without_company_permission(method, path, payload, service):
    api = FastAPI()
    api.include_router(payroll_advances.router)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    api.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=2)
    async def session():
        yield db
    api.dependency_overrides[get_db] = session
    with patch.object(payroll_advances, service, AsyncMock()) as operation:
        async with AsyncClient(transport=ASGITransport(app=api), base_url='http://test') as client:
            response = await client.request(method, path, json=payload)
        assert response.status_code == 403, response.text
        operation.assert_not_awaited()
        db.commit.assert_not_awaited()
