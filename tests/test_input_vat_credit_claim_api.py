from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.api.v1 import input_vat_credit_claims as api
from app.services.input_vat_credit_claim_service import InputVatCreditClaimError
from test_input_vat_credit_eligibility import payload


@pytest.mark.parametrize('method,path', [('post','/assess'),('post','/claims'),('get','/claims'),('post','/claims/1/reverse')])
def test_authentication_required(method,path):
    app=FastAPI(); app.include_router(api.router)
    assert getattr(TestClient(app),method)('/companies/1/input-vat-credit'+path).status_code==401


def test_claim_requires_company_approval_permission():
    from app.api.deps import get_current_user
    from app.core.database import get_db
    app=FastAPI(); app.include_router(api.router)
    db=AsyncMock(); db.execute.return_value=Mock(scalar_one_or_none=lambda:None)
    app.dependency_overrides[get_current_user]=lambda:NS(id=7)
    app.dependency_overrides[get_db]=lambda:db
    response=TestClient(app).post('/companies/2/input-vat-credit/claims',json=payload().model_dump(mode='json'))
    assert response.status_code==403
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_uses_authenticated_actor_and_company(monkeypatch):
    row=NS(id=1,company_id=3,tax_calculation_id=1,evidence_id=4,reversal_evidence_id=None,
        request_key='a',policy_version='v1',claim_period=date(2026,9,1),attestation={},decision={})
    fn=AsyncMock(return_value=row); monkeypatch.setattr(api,'create_input_vat_credit_claim',fn)
    db=AsyncMock(); data=payload()
    result=await api.create(3,data,db,NS(id=7),None)
    assert result.id==1
    assert fn.await_args.kwargs==dict(company_id=3,data=data,created_by=7)
    db.commit.assert_awaited_once(); db.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('error',[InputVatCreditClaimError('registration_not_confirmed'),HTTPException(409,'closed')])
async def test_failure_rolls_back_entire_claim(monkeypatch,error):
    monkeypatch.setattr(api,'create_input_vat_credit_claim',AsyncMock(side_effect=error))
    db=AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api.create(1,payload(),db,NS(id=7),None)
    assert exc.value.status_code==409
    db.rollback.assert_awaited_once(); db.commit.assert_not_awaited()
