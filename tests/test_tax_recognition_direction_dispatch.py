import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest

import app.services.tax_recognition_lifecycle_service as service

from app.services.trade_document_types import (
    TradeDirection,
)


D1 = date(
    2026,
    8,
    20,
)


def test_invoice_line_sale_dispatches_output(
    monkeypatch,
):
    direction = AsyncMock(
        return_value=TradeDirection.SALE
    )

    output = AsyncMock(
        return_value=("output",)
    )

    input_vat = AsyncMock(
        side_effect=AssertionError(
            "INPUT must not run for SALE"
        )
    )

    monkeypatch.setattr(
        service,
        "_get_tax_invoice_direction",
        direction,
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_for_invoice_line",
        output,
    )

    monkeypatch.setattr(
        service,
        "reconcile_input_tax_for_invoice_line",
        input_vat,
    )

    result = asyncio.run(
        service.reconcile_tax_for_invoice_line(
            object(),
            company_id=1,
            invoice_id=10,
            invoice_line_id=20,
            adjustment_date=D1,
            created_by=7,
        )
    )

    assert result == (
        "output",
    )

    output.assert_awaited_once()

    input_vat.assert_not_awaited()


def test_invoice_line_purchase_dispatches_input(
    monkeypatch,
):
    direction = AsyncMock(
        return_value=TradeDirection.PURCHASE
    )

    output = AsyncMock(
        side_effect=AssertionError(
            "OUTPUT must not run for PURCHASE"
        )
    )

    input_vat = AsyncMock(
        return_value=("input",)
    )

    monkeypatch.setattr(
        service,
        "_get_tax_invoice_direction",
        direction,
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_for_invoice_line",
        output,
    )

    monkeypatch.setattr(
        service,
        "reconcile_input_tax_for_invoice_line",
        input_vat,
    )

    result = asyncio.run(
        service.reconcile_tax_for_invoice_line(
            object(),
            company_id=1,
            invoice_id=10,
            invoice_line_id=20,
            adjustment_date=D1,
            created_by=7,
        )
    )

    assert result == (
        "input",
    )

    input_vat.assert_awaited_once()

    output.assert_not_awaited()


def test_invoice_sale_dispatches_output(
    monkeypatch,
):
    direction = AsyncMock(
        return_value=TradeDirection.SALE
    )

    output = AsyncMock(
        return_value=("output",)
    )

    input_vat = AsyncMock(
        side_effect=AssertionError(
            "INPUT must not run for SALE"
        )
    )

    monkeypatch.setattr(
        service,
        "_get_tax_invoice_direction",
        direction,
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_for_invoice",
        output,
    )

    monkeypatch.setattr(
        service,
        "reconcile_input_tax_for_invoice",
        input_vat,
    )

    result = asyncio.run(
        service.reconcile_tax_for_invoice(
            object(),
            company_id=1,
            invoice_id=10,
            adjustment_date=D1,
            created_by=7,
        )
    )

    assert result == (
        "output",
    )

    output.assert_awaited_once()

    input_vat.assert_not_awaited()


def test_invoice_purchase_dispatches_input(
    monkeypatch,
):
    direction = AsyncMock(
        return_value=TradeDirection.PURCHASE
    )

    output = AsyncMock(
        side_effect=AssertionError(
            "OUTPUT must not run for PURCHASE"
        )
    )

    input_vat = AsyncMock(
        return_value=("input",)
    )

    monkeypatch.setattr(
        service,
        "_get_tax_invoice_direction",
        direction,
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_for_invoice",
        output,
    )

    monkeypatch.setattr(
        service,
        "reconcile_input_tax_for_invoice",
        input_vat,
    )

    result = asyncio.run(
        service.reconcile_tax_for_invoice(
            object(),
            company_id=1,
            invoice_id=10,
            adjustment_date=D1,
            created_by=7,
        )
    )

    assert result == (
        "input",
    )

    input_vat.assert_awaited_once()

    output.assert_not_awaited()


def test_invalid_invoice_line_fails_before_direction_lookup(
    monkeypatch,
):
    direction = AsyncMock()

    monkeypatch.setattr(
        service,
        "_get_tax_invoice_direction",
        direction,
    )

    with pytest.raises(
        ValueError,
        match=(
            "invoice_line_id must be "
            "greater than zero"
        ),
    ):
        asyncio.run(
            service.reconcile_tax_for_invoice_line(
                object(),
                company_id=1,
                invoice_id=10,
                invoice_line_id=0,
                adjustment_date=D1,
                created_by=7,
            )
        )

    direction.assert_not_awaited()
