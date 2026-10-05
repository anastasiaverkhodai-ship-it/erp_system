"""Imported payroll data and supplements remain company protected."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.deps import get_current_user
from app.core.database import get_db
from app.api.v1.payroll_opening import router as opening_router
from app.api.v1.payroll_supplements import router as supplement_router


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path', [
    ('POST','/opening-balances/1/payroll-detail'),
    ('GET','/opening-balances/1/payroll-detail'),
    ('POST','/employment-contracts/1/earnings-history'),
    ('GET','/employment-contracts/1/earnings-history'),
    ('POST','/employment-contracts/1/leave-opening'),
    ('GET','/employment-contracts/1/leave-opening'),
    ('GET','/employees/1/payroll-balance?as_of=2026-09-30'),
    ('GET','/payroll-inputs/1/supplements'),
    ('POST','/payroll-inputs/1/supplements'),
    ('POST','/payroll-supplements/1/cancel'),
])
async def test_source_endpoints_require_company_access(method, path):
    api = FastAPI()
    api.include_router(opening_router)
    api.include_router(supplement_router)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    api.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    async def session():
        yield db
    api.dependency_overrides[get_db] = session
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
        response = await client.request(method, '/companies/1'+path, json={} if method=='POST' else None)
    assert response.status_code == 403
    db.commit.assert_not_awaited()
