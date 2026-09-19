import asyncio
import inspect
from datetime import date
from decimal import Decimal
from typing import get_type_hints

import pytest
from fastapi import HTTPException

import app.api.v1.trial_balance as api
from app.main import app
from app.schemas.trial_balance import TrialBalanceReport


def test_route_integer_contract():
    hints = get_type_hints(
        api.read_trial_balance
    )

    assert hints["company_id"] is int


def test_openapi_contract():
    path = (
        "/api/v1/companies/{company_id}"
        "/trial-balance"
    )

    paths = app.openapi()["paths"]

    assert path in paths
    assert set(paths[path]) == {"get"}

    operation = paths[path]["get"]

    params = {
        item["name"]: item
        for item in operation["parameters"]
    }

    assert set(params) == {
        "company_id",
        "date_from",
        "date_to",
    }

    assert params["company_id"]["in"] == "path"
    assert params["company_id"]["required"] is True

    assert params["date_from"]["in"] == "query"
    assert params["date_from"]["required"] is True

    assert params["date_to"]["in"] == "query"
    assert params["date_to"]["required"] is True


@pytest.mark.asyncio
async def test_success_dispatch(monkeypatch):
    calls = []

    async def fake_service(
        session,
        *,
        company_id,
        date_from,
        date_to,
    ):
        calls.append(
            (
                session,
                company_id,
                date_from,
                date_to,
            )
        )

        return TrialBalanceReport(
            company_id=company_id,
            date_from=date_from,
            date_to=date_to,
            lines=[],
            total_opening_debit=Decimal("0"),
            total_opening_credit=Decimal("0"),
            total_period_debit=Decimal("0"),
            total_period_credit=Decimal("0"),
            total_closing_debit=Decimal("0"),
            total_closing_credit=Decimal("0"),
        )

    monkeypatch.setattr(
        api,
        "get_trial_balance",
        fake_service,
    )

    db = object()
    start = date(2026, 8, 1)
    end = date(2026, 8, 31)

    result = await api.read_trial_balance(
        company_id=3,
        date_from=start,
        date_to=end,
        _=None,
        db=db,
    )

    assert result.company_id == 3

    assert calls == [
        (
            db,
            3,
            start,
            end,
        )
    ]


@pytest.mark.asyncio
async def test_invalid_date_range_maps_to_422(
    monkeypatch,
):
    async def fake_service(
        session,
        *,
        company_id,
        date_from,
        date_to,
    ):
        raise ValueError(
            "date_from must be less than "
            "or equal to date_to"
        )

    monkeypatch.setattr(
        api,
        "get_trial_balance",
        fake_service,
    )

    with pytest.raises(
        HTTPException
    ) as exc:
        await api.read_trial_balance(
            company_id=3,
            date_from=date(2026, 9, 2),
            date_to=date(2026, 9, 1),
            _=None,
            db=object(),
        )

    assert exc.value.status_code == 422
    assert "date_from" in exc.value.detail


def test_permission_and_read_only_source():
    source = inspect.getsource(api)

    assert (
        source.count(
            '"journal_entries.read"'
        )
        == 1
    )

    assert (
        "HTTP_422_UNPROCESSABLE_CONTENT"
        in source
    )

    assert (
        "HTTP_422_UNPROCESSABLE_ENTITY"
        not in source
    )

    for token in (
        "await db.commit()",
        "await db.rollback()",
        "db.add(",
        "db.delete(",
        "JournalEntry(",
        "JournalEntryLine(",
    ):
        assert token not in source


def test_api_is_async():
    assert asyncio.iscoroutinefunction(
        api.read_trial_balance
    )
