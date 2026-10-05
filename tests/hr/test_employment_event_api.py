from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.deps import get_current_user
from app.core.database import get_db
from app.api.v1.employment_events import router


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path,body',[
    ('GET','/employment-contracts/1/events',None),
    ('GET','/employment-contracts/1/state?on_date=2026-01-10',None),
    ('POST','/employment-contracts/1/events',dict(event_type='hire',effective_date='2026-01-10',order_number='1',order_date='2026-01-10',reason='Hire',request_key='hire')),
    ('POST','/employment-contracts/1/events/1/reverse',dict(order_number='2',order_date='2026-01-11',reason='Correction',request_key='reverse')),
    ('POST','/employment-events/batch',dict(events=[dict(contract_id=1,event=dict(event_type='hire',effective_date='2026-01-10',order_number='1',order_date='2026-01-10',reason='Hire',request_key='hire'))])),
])
async def test_company_permission_required(method,path,body):
    api=FastAPI();api.include_router(router)
    db=AsyncMock();db.execute.return_value=SimpleNamespace(scalar_one_or_none=lambda:None)
    api.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=1)
    async def session(): yield db
    api.dependency_overrides[get_db]=session
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
        result=await client.request(method,'/companies/1'+path,json=body)
    assert result.status_code==403
    db.commit.assert_not_awaited()
