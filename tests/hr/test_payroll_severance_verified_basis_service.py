from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as N

import pytest

import app.services.payroll_severance_verified_basis_service as svc
from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
)
from app.services.payroll_termination_severance_policy import (
    SeverancePolicy,
)


def fixture_preflight(category="average_months", multiplier=1):
    return N(
        company_id=1,
        termination_event_id=25,
        termination_date=date(2026, 9, 30),
        termination_order_number="TERM-2026-09",
        policy=SeverancePolicy(
            category=category,
            multiplier=multiplier,
            labour_code_provision="40.1",
            source_reference="Verified termination order",
        ),
    )


def fixture_average(amount=D("12000.00")):
    return N(
        average_monthly=amount,
        reference_start=date(2026, 7, 1),
        reference_end=date(2026, 8, 31),
        source_reference=(
            "Finalized ERP payroll 101 and 102; "
            "attendance reconciled"
        ),
    )


async def prepare(monkeypatch, preflight=None, average=None):
    state = {"average_called": False}

    async def fake_preflight(db, **kwargs):
        assert kwargs["company_id"] == 1
        assert kwargs["payroll_input_id"] == 50
        return (
            preflight
            if preflight is not None
            else fixture_preflight()
        )

    async def fake_average(db, **kwargs):
        state["average_called"] = True
        assert kwargs == {
            "company_id": 1,
            "employment_contract_id": 10,
            "termination_date": date(2026, 9, 30),
        }
        return average if average is not None else fixture_average()

    monkeypatch.setattr(
        svc, "preflight_termination_severance", fake_preflight
    )
    monkeypatch.setattr(
        svc, "derive_database_severance_average", fake_average
    )

    result = await svc.prepare_verified_severance_basis(
        object(),
        company_id=1,
        employment_contract_id=10,
        payroll_input_id=50,
    )
    return result, state


@pytest.mark.asyncio
async def test_documented_average_month(monkeypatch):
    result, state = await prepare(monkeypatch)
    assert state["average_called"]
    assert result.base_amount == D("12000.00")
    assert result.gross_amount == D("12000.00")
    assert result.basis_as_of == date(2026, 9, 30)
    assert result.termination_event_id == 25
    assert result.currency_code == "UAH"
    assert "101 and 102" in result.basis_reference

    with pytest.raises(FrozenInstanceError):
        result.gross_amount = D("1.00")


@pytest.mark.asyncio
async def test_six_month_statutory_multiplier(monkeypatch):
    result, _ = await prepare(
        monkeypatch,
        preflight=fixture_preflight(multiplier=6),
    )
    assert result.base_amount == D("12000.00")
    assert result.gross_amount == D("72000.00")
    assert result.multiplier == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("category", [
    "minimum_wages", "manual_review"
])
async def test_unsupported_policy_blocked(monkeypatch, category):
    with pytest.raises(SeveranceAverageError):
        await prepare(
            monkeypatch,
            preflight=fixture_preflight(category=category),
        )


@pytest.mark.asyncio
async def test_zero_average_rejected(monkeypatch):
    with pytest.raises(SeveranceAverageError):
        await prepare(
            monkeypatch,
            average=fixture_average(D("0.00")),
        )


@pytest.mark.asyncio
async def test_nonfinite_average_rejected(monkeypatch):
    with pytest.raises(SeveranceAverageError):
        await prepare(
            monkeypatch,
            average=fixture_average(D("NaN")),
        )


@pytest.mark.asyncio
async def test_missing_termination_order_rejected(monkeypatch):
    preflight = fixture_preflight()
    preflight.termination_order_number = " "
    with pytest.raises(SeveranceAverageError):
        await prepare(monkeypatch, preflight=preflight)


@pytest.mark.asyncio
async def test_company_mismatch_rejected(monkeypatch):
    preflight = fixture_preflight()
    preflight.company_id = 2
    with pytest.raises(SeveranceAverageError):
        await prepare(monkeypatch, preflight=preflight)


@pytest.mark.asyncio
async def test_reference_db_limit_rejected(monkeypatch):
    average = fixture_average()
    average.source_reference = "X" * 1100
    with pytest.raises(SeveranceAverageError):
        await prepare(monkeypatch, average=average)


@pytest.mark.asyncio
async def test_invalid_identifiers_rejected_without_db():
    for values in [
        {"company_id": 0},
        {"employment_contract_id": True},
        {"payroll_input_id": -1},
    ]:
        kwargs = dict(
            company_id=1,
            employment_contract_id=10,
            payroll_input_id=50,
        )
        kwargs.update(values)

        with pytest.raises(SeveranceAverageError):
            await svc.prepare_verified_severance_basis(
                object(), **kwargs
            )
