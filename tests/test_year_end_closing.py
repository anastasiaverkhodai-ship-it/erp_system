from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.api.v1.year_end_closings as api
from app.schemas.year_end_closing import YearEndCloseRequest, YearEndPreviewRequest
from app.api.v1.journal_entries import update_journal_entry, delete_journal_entry
from app.schemas.journal_entry import JournalEntryUpdate


@pytest.mark.parametrize("data", [
    dict(profit_account_id=0,loss_account_id=1),
    dict(profit_account_id=1,loss_account_id=1,mappings=[dict(source_account_id=2,result_account_id=3)]*2),
    dict(profit_account_id=1,loss_account_id=1,company_id=3),
])
def test_invalid_mapping_schema(data):
    with pytest.raises(ValidationError):
        YearEndPreviewRequest(**data)


@pytest.mark.parametrize("key,fingerprint", [(" ","0"*64),("key","invalid"),("key",None)])
def test_close_requires_request_and_preview_identity(key,fingerprint):
    with pytest.raises(ValidationError):
        YearEndCloseRequest(profit_account_id=1,loss_account_id=1,request_key=key,preview_fingerprint=fingerprint)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,service", [("close","close_year"),("reverse","reverse_year_end")])
async def test_response_failure_rolls_back_before_commit(monkeypatch,name,service):
    db=SimpleNamespace(commit=AsyncMock(),rollback=AsyncMock())
    monkeypatch.setattr(api,service,AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(api,"_response",AsyncMock(side_effect=RuntimeError("response")))
    kwargs=dict(company_id=1,user=SimpleNamespace(id=1),db=db)
    if name=="close":
        kwargs.update(year=2025,data=SimpleNamespace())
    else:
        kwargs.update(closing_id=1)
    with pytest.raises(RuntimeError):
        await getattr(api,name)(**kwargs)
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [update_journal_entry,delete_journal_entry])
async def test_generic_editor_cannot_change_year_end_journals(endpoint):
    result=SimpleNamespace(scalar_one_or_none=lambda:SimpleNamespace(year_end_closing_id=1))
    db=SimpleNamespace(execute=AsyncMock(return_value=result),rollback=AsyncMock(),commit=AsyncMock())
    kwargs=dict(company_id=1,journal_entry_id=1,db=db)
    if endpoint is update_journal_entry:
        kwargs["data"]=JournalEntryUpdate(description="changed")
    with pytest.raises(HTTPException) as caught:
        await endpoint(**kwargs)
    assert caught.value.status_code==409
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [("post","/2025/preview"),("post","/2025/close"),("post","/1/reverse"),("get","/1"),("get","")])
async def test_authentication_required(method,path):
    from httpx import ASGITransport,AsyncClient
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        response=await client.request(method,"/api/v1/companies/1/year-end-closings"+path,json={})
    assert response.status_code in (401,403)
