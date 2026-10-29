"""Advance tax registration uses the existing permission and transaction boundary."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.deps import get_current_user
from app.core.database import get_db
from app.api.v1 import payroll_tax_remittance as module

PAYLOAD=dict(payroll_advance_id=2,journal_entry_id=3,bank_account_id=4,
    component='unified_social_contribution',amount='220.00',request_key='usc-paid',
    document_reference='Confirmed bank receipt',budget_payment_reference='USC destination')


def setup(permissions):
    api=FastAPI();api.include_router(module.router)
    db=AsyncMock()
    db.execute.side_effect=[SimpleNamespace(scalar_one_or_none=lambda allowed=allowed: 1 if allowed else None)
                           for allowed in permissions]
    api.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=7)
    async def session():yield db
    api.dependency_overrides[get_db]=session
    return api,db


@pytest.mark.asyncio
@pytest.mark.parametrize('permissions',[(False,),(True,False),(True,True)])
async def test_usc_advance_requires_both_company_permissions(permissions):
    api,db=setup(permissions)
    row=SimpleNamespace(**PAYLOAD,id=5,company_id=1,created_by=7,created_at=datetime.now(timezone.utc))
    with patch.object(module,'register_advance_tax_remittance',AsyncMock(return_value=row)) as register:
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            response=await client.post('/companies/1/payroll-tax-remittances/advances',json=PAYLOAD)
    assert response.status_code==(201 if all(permissions) else 403),response.text
    if all(permissions):
        assert register.await_args.kwargs['company_id']==1
        assert response.json()['component']=='unified_social_contribution'
        db.commit.assert_awaited_once()
    else:
        register.assert_not_awaited();db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_advance_commit_failure_rolls_back():
    api,db=setup((True,True));db.commit.side_effect=RuntimeError('Deferred constraint failure')
    row=SimpleNamespace(**PAYLOAD,id=5,company_id=1,created_by=7,created_at=datetime.now(timezone.utc))
    with patch.object(module,'register_advance_tax_remittance',AsyncMock(return_value=row)):
        async with AsyncClient(transport=ASGITransport(app=api,raise_app_exceptions=False),base_url='http://test') as client:
            response=await client.post('/companies/1/payroll-tax-remittances/advances',json=PAYLOAD)
    assert response.status_code==500
    db.rollback.assert_awaited_once()
