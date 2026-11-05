from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
from fastapi import FastAPI
from httpx import ASGITransport,AsyncClient
from app.api.deps import get_current_user
from app.core.database import get_db
from app.api.v1 import payroll_d6_evidence as module


def setup(allowed):
    app=FastAPI();app.include_router(module.router)
    db=AsyncMock()
    db.execute.return_value=SimpleNamespace(scalar_one_or_none=lambda:1 if allowed else None)
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=7)
    async def session():yield db
    app.dependency_overrides[get_db]=session
    return app,db


@pytest.mark.asyncio
@pytest.mark.parametrize('allowed',[False,True])
@pytest.mark.parametrize('action',['create','review','summary','read'])
async def test_authenticated_company_scope(allowed,action):
    app,db=setup(allowed)
    definitions={
        'create':('post','/companies/42/payroll-d6-evidence',dict(payroll_period_id=3,employment_contract_id=4,
            applies=False,document_date='2026-09-30',document_reference='Documented non-applicability',request_key='request-001'),
            'create_evidence',SimpleNamespace(id=9,revision=1,fingerprint='a'*64)),
        'review':('post','/companies/42/payroll-d6-evidence/9/review',dict(approved=True,review_reference='Independent source review'),
            'review_evidence',SimpleNamespace(id=10,evidence_id=9,decision='approved')),
        'summary':('get','/companies/42/payroll-periods/3/d6-evidence',None,'period_evidence',{'coverage':'INCOMPLETE'}),
        'read':('get','/companies/42/payroll-d6-evidence/9',None,'get_evidence',{'id':9})}
    method,url,body,name,result=definitions[action]
    with patch.object(module,name,AsyncMock(return_value=result)) as operation:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            response=await client.request(method,url,**({'json':body} if body else {}))
    assert response.status_code==((201 if action=='create' else 200) if allowed else 403),response.text
    if allowed:
        assert operation.await_args.kwargs['company_id']==42
        if method=='post':
            assert operation.await_args.kwargs['actor_id']==7
            db.commit.assert_awaited_once()
    else:
        operation.assert_not_awaited();db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_failure_rolls_back():
    app,db=setup(True)
    with patch.object(module,'review_evidence',AsyncMock(side_effect=ValueError('Stale correction'))):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            result=await client.post('/companies/42/payroll-d6-evidence/9/review',json={'approved':True,'review_reference':'Independent source review'})
    assert result.status_code==400
    db.rollback.assert_awaited_once();db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unapplied_migration_is_service_unavailable_not_a_sql_error():
    app,db=setup(True)
    with patch.object(module,'review_evidence',AsyncMock(side_effect=module.D6MigrationRequired('Migration required'))):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/companies/42/payroll-d6-evidence/9/review',json={'approved':True,'review_reference':'Independent source review'})
    assert response.status_code==503
    db.rollback.assert_awaited_once();db.commit.assert_not_awaited()
