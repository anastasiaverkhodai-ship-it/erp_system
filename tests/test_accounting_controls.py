from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import accounting_control_service as service


@pytest.mark.asyncio
async def test_consolidated_control_uses_only_verified_output_vat(
    monkeypatch,
):
    output = SimpleNamespace(
        matched=True,
        expected_output_vat=Decimal("200.00"),
        posted_output_vat=Decimal("200.00"),
        difference=Decimal("0.00"),
        issues=[],
    )

    reconcile = AsyncMock(return_value=output)

    monkeypatch.setattr(
        service,
        "reconcile_output_vat_gl",
        reconcile,
    )

    session = AsyncMock()

    result = (
        await service.get_consolidated_accounting_controls(
            session,
            company_id=7,
            date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 30),
        )
    )

    assert result.company_id == 7
    assert result.matched is False
    assert result.status == "incomplete"
    assert result.coverage_complete is False
    assert result.checked_families_matched is True
    assert result.implemented_family_count == 1
    assert result.not_implemented_family_count == 5

    by_family = {
        item.family: item
        for item in result.families
    }

    assert by_family["output_vat"].status == "matched"
    assert (
        by_family["output_vat"].difference
        == Decimal("0.00")
    )

    for family in (
        "ar",
        "ap",
        "cash_bank",
        "inventory",
        "input_vat",
    ):
        assert (
            by_family[family].status
            == "not_implemented"
        )

    reconcile.assert_awaited_once_with(
        session,
        company_id=7,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 30),
    )


@pytest.mark.asyncio
async def test_consolidated_control_propagates_output_vat_mismatch(
    monkeypatch,
):
    output = SimpleNamespace(
        matched=False,
        expected_output_vat=Decimal("200.00"),
        posted_output_vat=Decimal("190.00"),
        difference=Decimal("10.00"),
        issues=[SimpleNamespace(code="amount_mismatch")],
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_vat_gl",
        AsyncMock(return_value=output),
    )

    result = (
        await service.get_consolidated_accounting_controls(
            AsyncMock(),
            company_id=7,
            date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 30),
        )
    )

    assert result.matched is False
    assert result.status == "mismatch"
    assert result.coverage_complete is False
    assert result.checked_families_matched is False

    output_family = next(
        item
        for item in result.families
        if item.family == "output_vat"
    )

    assert output_family.status == "mismatch"
    assert output_family.issue_count == 1
    assert output_family.difference == Decimal("10.00")


@pytest.mark.asyncio
async def test_consolidated_control_rejects_invalid_date_range(
    monkeypatch,
):
    reconcile = AsyncMock()

    monkeypatch.setattr(
        service,
        "reconcile_output_vat_gl",
        reconcile,
    )

    with pytest.raises(
        ValueError,
        match="date_from",
    ):
        await service.get_consolidated_accounting_controls(
            AsyncMock(),
            company_id=7,
            date_from=date(2026, 10, 1),
            date_to=date(2026, 9, 30),
        )

    reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_account_configuration_is_domain_conflict(monkeypatch):
    from fastapi import HTTPException
    import app.api.v1.accounting_controls as api
    from app.services.accounting_account_role_resolver import AccountingRoleAccountInvalidError
    monkeypatch.setattr(api,"get_consolidated_accounting_controls",AsyncMock(
        side_effect=AccountingRoleAccountInvalidError("Missing VAT system account")))
    with pytest.raises(HTTPException) as caught:
        await api.read_accounting_controls(company_id=1,date_from=date(2026,1,1),date_to=date(2026,1,31),db=AsyncMock())
    assert caught.value.status_code==409
    assert "Missing VAT" in caught.value.detail
