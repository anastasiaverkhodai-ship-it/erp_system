from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.deps import get_current_user
from app.api.v1 import payroll_regulatory_reports
from app.core.database import get_db


@pytest.mark.asyncio
@pytest.mark.parametrize('allowed', [False, True])
async def test_readiness_requires_company_read_permission(allowed):
    api = FastAPI()
    api.include_router(payroll_regulatory_reports.router)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: 1 if allowed else None)
    api.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    async def session():
        yield db
    api.dependency_overrides[get_db] = session
    report = dict(company_id=1, payroll_period_id=2, report_kind='d6', ready=False,
        applicability='not_determined', blocking_issues=['SPECIAL_SERVICE_EVIDENCE_MISSING'],
        calculation_sources=[], notice='Not a completed form')
    with patch('app.services.payroll_regulatory_readiness_service.payroll_regulatory_readiness',
               AsyncMock(return_value=report)) as build:
        async with AsyncClient(transport=ASGITransport(app=api), base_url='http://test') as client:
            response = await client.get('/companies/1/payroll-periods/2/regulatory-readiness/d6')
        assert response.status_code == (200 if allowed else 403), response.text
        if allowed:
            build.assert_awaited_once_with(db, company_id=1, payroll_period_id=2, report_kind='d6')
            assert response.json()['ready'] is False
        else:
            build.assert_not_awaited()
        db.commit.assert_not_awaited()
