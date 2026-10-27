"""Permission and transaction boundaries for financial sick corrections."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.deps import get_current_user
from app.core.database import get_db
from app.api.v1 import payroll_sick_correction_applications as module

PAYLOAD = dict(original_sick_id=1, insurance_evidence_id=2, correction_date='2026-10-26',
    request_key='apply', reason='Reviewed insurance evidence')


def api_setup(permissions):
    api = FastAPI()
    api.include_router(module.router)
    db = AsyncMock()
    db.execute.side_effect = [SimpleNamespace(scalar_one_or_none=lambda allowed=allowed: 1 if allowed else None)
                             for allowed in permissions]
    api.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
    async def session():
        yield db
    api.dependency_overrides[get_db] = session
    return api,db


@pytest.mark.asyncio
@pytest.mark.parametrize('permissions',[(False,), (True,False), (True,True)])
async def test_apply_requires_both_company_permissions(permissions):
    api,db = api_setup(permissions)
    with patch.object(module,'apply_sick_correction',AsyncMock(return_value=SimpleNamespace(id=4))) as apply, \
         patch.object(module,'application_response',AsyncMock(return_value={'id':4,'status':'applied'})):
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            response = await client.post('/companies/1/payroll-sick-corrections/apply',json=PAYLOAD)
    assert response.status_code==(201 if all(permissions) else 403),response.text
    if all(permissions):
        assert apply.await_args.kwargs['company_id']==1
        assert apply.await_args.kwargs['actor_id']==7
        db.commit.assert_awaited_once()
    else:
        apply.assert_not_awaited()
        db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[ValueError('Rejected correction'),RuntimeError('Deferred constraint failure')])
async def test_apply_rolls_back_calculation_or_commit_failure(failure):
    api,db = api_setup((True,True))
    if isinstance(failure,RuntimeError):
        db.commit.side_effect=failure
    with patch.object(module,'apply_sick_correction',AsyncMock(
            side_effect=failure if isinstance(failure,ValueError) else None,
            return_value=SimpleNamespace(id=4))), \
         patch.object(module,'application_response',AsyncMock(return_value={'id':4})):
        async with AsyncClient(transport=ASGITransport(app=api,raise_app_exceptions=False),base_url='http://test') as client:
            response=await client.post('/companies/1/payroll-sick-corrections/apply',json=PAYLOAD)
    assert response.status_code==(409 if isinstance(failure,ValueError) else 500)
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_application_read_is_company_scoped():
    api,db=api_setup((True,))
    db.scalar.return_value=None
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
        response=await client.get('/companies/42/payroll-sick-corrections/7')
    assert response.status_code==404
    query=db.scalar.await_args.args[0]
    assert set(query.compile().params.values())=={42,7}
    db.commit.assert_not_awaited()
