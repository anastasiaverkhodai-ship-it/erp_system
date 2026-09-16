from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.api.v1 import order_vat_advances as api
from app.services.order_vat_advance_service import OrderVatAdvanceError


@pytest.mark.parametrize('method,path', [('get',''), ('get','/1/lines'), ('post',''),
    ('post','/1/reverse'), ('post','/orders/1/transfer'), ('post','/orders/1/undo-transfer')])
def test_advance_routes_require_authentication(method, path):
    app = FastAPI(); app.include_router(api.router)
    assert getattr(TestClient(app), method)('/companies/1/order-vat-advances' + path).status_code == 401


@pytest.mark.asyncio
async def test_create_uses_scoped_company_actor_and_commits(monkeypatch):
    service = AsyncMock(return_value=SimpleNamespace(id=1, order_id=2, payment_id=3, invoice_id=None,
        status='active', amount=Decimal(72), event_date=date(2026,9,1)))
    monkeypatch.setattr(api, 'create_order_vat_advance', service)
    db = AsyncMock()
    response = await api.create(10, api.AdvanceCreate(order_id=2, payment_id=3), db, SimpleNamespace(id=7), None)
    assert response.id == 1
    assert service.await_args.kwargs == dict(company_id=10, order_id=2, payment_id=3, created_by=7)
    db.commit.assert_awaited_once(); db.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [OrderVatAdvanceError('Invalid source'), HTTPException(409,'closed')])
async def test_failed_advance_rolls_back_all_writes(monkeypatch, error):
    monkeypatch.setattr(api, 'create_order_vat_advance', AsyncMock(side_effect=error))
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api.create(1, api.AdvanceCreate(order_id=2, payment_id=3), db, SimpleNamespace(id=7), None)
    assert exc.value.status_code == 409
    db.rollback.assert_awaited_once(); db.commit.assert_not_awaited()


def test_company_permission_denied_before_advance_access():
    from unittest.mock import Mock
    from app.api.deps import get_current_user
    from app.core.database import get_db
    app = FastAPI(); app.include_router(api.router)
    db = AsyncMock()
    db.execute.return_value = Mock(scalar_one_or_none=lambda: None)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
    app.dependency_overrides[get_db] = lambda: db
    response = TestClient(app).post('/companies/2/order-vat-advances', json={'order_id':1, 'payment_id':1})
    assert response.status_code == 403
    db.commit.assert_not_awaited()
