"""Opening request validation and atomic API transaction boundaries."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.api.v1.opening_balances as api
from app.schemas.opening_balance import OpeningBalanceCreate, OpeningBalanceLineCreate
from app.schemas.journal_entry import JournalEntryUpdate
from app.api.v1.journal_entries import update_journal_entry, delete_journal_entry


@pytest.mark.parametrize("values", [
    {"debit":0}, {"debit":1,"credit":1}, {"debit":-1}, {"debit":"1.001"},
    {"debit":"NaN"}, {"debit":"Infinity"}, {"debit":1,"account_id":0},
    {"debit":1,"company_id":2}, {"debit":"10000000000000000.01"},
])
def test_invalid_line_rejected(values):
    with pytest.raises(ValidationError):
        OpeningBalanceLineCreate(**dict({"account_id":1}, **values))


@pytest.mark.parametrize("key", ["", "   ", "\n\t", "x"*256, None])
def test_nonempty_request_key_required(key):
    with pytest.raises(ValidationError):
        OpeningBalanceCreate(request_key=key, opening_date=date(2026,8,1),
            lines=[dict(account_id=1,debit=1),dict(account_id=2,credit=1)])


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "post", "reverse"])
async def test_response_failure_rolls_back_before_commit(monkeypatch, operation):
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(api, "_response", AsyncMock(side_effect=RuntimeError("serialization")))
    monkeypatch.setattr(api, operation+"_opening_balance", AsyncMock(return_value=SimpleNamespace(id=7)))
    kwargs = dict(company_id=1,current_user=SimpleNamespace(id=1),db=db)
    if operation == "create":
        kwargs["data"] = SimpleNamespace()
    else:
        kwargs["opening_balance_id"] = 7
    if operation == "reverse":
        kwargs["data"] = SimpleNamespace(reversal_date=date(2026,8,2))
    with pytest.raises(RuntimeError, match="serialization"):
        await getattr(api,operation+"_opening_balance_endpoint")(**kwargs)
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [update_journal_entry, delete_journal_entry])
async def test_generic_editor_cannot_modify_opening_journal(endpoint):
    result = SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(opening_balance_id=7))
    db = SimpleNamespace(execute=AsyncMock(return_value=result), rollback=AsyncMock(), commit=AsyncMock())
    kwargs = dict(company_id=1,journal_entry_id=9,db=db)
    if endpoint is update_journal_entry:
        kwargs["data"] = JournalEntryUpdate(description="change")
    with pytest.raises(HTTPException) as caught:
        await endpoint(**kwargs)
    assert caught.value.status_code == 409
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [("post",""),("get","/1"),("post","/1/post"),("post","/1/reverse")])
async def test_http_routes_require_authentication(method,path):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        response = await client.request(method,"/api/v1/companies/1/opening-balances"+path,json={})
    assert response.status_code in (401,403)
