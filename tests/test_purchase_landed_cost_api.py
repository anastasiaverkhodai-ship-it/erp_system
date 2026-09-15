from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.v1 import purchase_landed_costs as api
from app.schemas.purchase_landed_cost import PurchaseLandedCostCreate
from app.services.purchase_landed_cost_persistence_service import PurchaseLandedCostPersistenceError


def payload():
    return PurchaseLandedCostCreate(source_journal_entry_line_id=1, request_key="freight",
        trade_document_id=10, warehouse_document_id=20, amount=Decimal("100"), cost_date=date(2026, 9, 15))


@pytest.mark.parametrize("method,path", [("get", ""), ("post", ""), ("post", "/1/reverse")])
def test_endpoints_require_authentication(method, path):
    app = FastAPI()
    app.include_router(api.router)
    response = getattr(TestClient(app), method)("/companies/1/purchase-landed-costs" + path)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_commits_once_with_server_user_and_company(monkeypatch):
    event = SimpleNamespace(id=42, company_id=1, trade_document_id=10, warehouse_document_id=20,
                            amount=Decimal("100"), currency_code="UAH", cost_date=date(2026, 9, 15), reversal_of_id=None)
    service = AsyncMock(return_value=SimpleNamespace(event=event))
    monkeypatch.setattr(api, "capitalize_purchase_landed_cost", service)
    db = AsyncMock()
    result = await api.create_landed_cost(1, payload(), db, SimpleNamespace(id=7), None)
    assert result.id == 42
    assert service.await_args.kwargs["company_id"] == 1
    assert service.await_args.kwargs["created_by"] == 7
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [PurchaseLandedCostPersistenceError("invalid source"), HTTPException(409, "closed")])
async def test_create_error_rolls_back_without_commit(monkeypatch, error):
    monkeypatch.setattr(api, "capitalize_purchase_landed_cost", AsyncMock(side_effect=error))
    db = AsyncMock()
    with pytest.raises(HTTPException) as failure:
        await api.create_landed_cost(1, payload(), db, SimpleNamespace(id=7), None)
    assert failure.value.status_code == 409
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
