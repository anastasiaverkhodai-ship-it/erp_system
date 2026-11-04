"""Company authorization and transaction failure boundaries for real execution."""
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
from fastapi import FastAPI
from httpx import ASGITransport,AsyncClient
from app.api.deps import get_current_user
from app.core.database import get_db
from app.api.v1 import payroll_severance_corrections as module


def setup(permissions):
    api=FastAPI();api.include_router(module.router)
    db=AsyncMock()
    db.execute.side_effect=[SimpleNamespace(scalar_one_or_none=lambda allowed=allowed:1 if allowed else None)
        for allowed in permissions]
    api.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=7)
    async def session():yield db
    api.dependency_overrides[get_db]=session
    return api,db


@pytest.mark.asyncio
@pytest.mark.parametrize('permissions',[(False,),(True,False),(True,True,False),(True,True,True)])
async def test_execute_requires_manage_create_and_approve(permissions):
    api,db=setup(permissions)
    with patch('app.services.payroll_severance_execution_service.execute_severance_correction',AsyncMock(return_value=SimpleNamespace(id=9))) as execute, \
         patch.object(module,'application_response',AsyncMock(return_value={'id':9})):
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            response=await client.post('/companies/42/payroll-severance-corrections/proposals/3/execute',json={'correction_date':'2026-09-30'})
    assert response.status_code==(201 if all(permissions) else 403),response.text
    if all(permissions):
        assert execute.await_args.kwargs['company_id']==42
        assert execute.await_args.kwargs['actor_id']==7
        db.commit.assert_awaited_once()
    else:
        execute.assert_not_awaited();db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('commit_failure',[False,True])
async def test_failure_rolls_back_everything(commit_failure):
    api,db=setup((True,True,True))
    if commit_failure:db.commit.side_effect=RuntimeError('Deferred database guard')
    with patch('app.services.payroll_severance_execution_service.execute_severance_correction',
            AsyncMock(return_value=SimpleNamespace(id=9),side_effect=None if commit_failure else ValueError('Source differs'))), \
         patch.object(module,'application_response',AsyncMock(return_value={'id':9})):
        async with AsyncClient(transport=ASGITransport(app=api,raise_app_exceptions=False),base_url='http://test') as client:
            response=await client.post('/companies/42/payroll-severance-corrections/proposals/3/execute',json={'correction_date':'2026-09-30'})
    assert response.status_code==(500 if commit_failure else 409)
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('path',['cases','proposals','applications'])
async def test_read_is_scoped_to_company(path):
    api,db=setup((True,));db.scalar.return_value=None
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
        response=await client.get(f'/companies/42/payroll-severance-corrections/{path}/7')
    assert response.status_code==404
    assert {42,7} <= set(db.scalar.await_args.args[0].compile().params.values())
    db.commit.assert_not_awaited()
