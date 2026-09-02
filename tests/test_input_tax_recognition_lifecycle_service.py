import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest

import app.services.tax_recognition_lifecycle_service as lifecycle

from app.services.input_tax_recognition_reconciliation_service import (
    InputTaxRecognitionReconciliationStateError,
)
from app.services.tax_types import (
    TaxDirection,
)


D1 = date(
    2026,
    8,
    15,
)


def test_input_direction_filter_is_explicit():
    import inspect

    source = inspect.getsource(
        lifecycle._get_input_tax_calculation_ids
    )

    assert (
        "TaxDirection.INPUT"
        in source
    )


def test_input_invoice_line_lifecycle_forwards_ids(
    monkeypatch,
):
    db = object()

    get_ids = AsyncMock(
        return_value=(
            11,
            12,
        )
    )

    expected = (
        object(),
        object(),
    )

    reconcile = AsyncMock(
        return_value=expected
    )

    monkeypatch.setattr(
        lifecycle,
        "_get_input_tax_calculation_ids",
        get_ids,
    )

    monkeypatch.setattr(
        lifecycle,
        "_reconcile_input_ids",
        reconcile,
    )

    result = asyncio.run(
        lifecycle.reconcile_input_tax_for_invoice_line(
            db,
            company_id=1,
            invoice_id=20,
            invoice_line_id=21,
            adjustment_date=D1,
            created_by=7,
        )
    )

    assert result is expected

    get_ids.assert_awaited_once_with(
        db,
        company_id=1,
        invoice_id=20,
        invoice_line_id=21,
    )

    reconcile.assert_awaited_once_with(
        db,
        company_id=1,
        calculation_ids=(
            11,
            12,
        ),
        adjustment_date=D1,
        created_by=7,
    )


def test_input_invoice_lifecycle_forwards_all_ids(
    monkeypatch,
):
    db = object()

    get_ids = AsyncMock(
        return_value=(
            31,
            32,
        )
    )

    expected = (
        object(),
    )

    reconcile = AsyncMock(
        return_value=expected
    )

    monkeypatch.setattr(
        lifecycle,
        "_get_input_tax_calculation_ids",
        get_ids,
    )

    monkeypatch.setattr(
        lifecycle,
        "_reconcile_input_ids",
        reconcile,
    )

    result = asyncio.run(
        lifecycle.reconcile_input_tax_for_invoice(
            db,
            company_id=1,
            invoice_id=30,
            adjustment_date=D1,
            created_by=8,
        )
    )

    assert result is expected

    get_ids.assert_awaited_once_with(
        db,
        company_id=1,
        invoice_id=30,
        invoice_line_id=None,
    )

    reconcile.assert_awaited_once_with(
        db,
        company_id=1,
        calculation_ids=(
            31,
            32,
        ),
        adjustment_date=D1,
        created_by=8,
    )


def test_input_reconcile_ids_calls_active_source_wrapper(
    monkeypatch,
):
    db = object()

    first = object()
    second = object()

    wrapper = AsyncMock(
        side_effect=(
            first,
            second,
        )
    )

    monkeypatch.setattr(
        lifecycle,
        "reconcile_input_tax_calculation_from_active_sources",
        wrapper,
    )

    result = asyncio.run(
        lifecycle._reconcile_input_ids(
            db,
            company_id=1,
            calculation_ids=(
                41,
                42,
            ),
            adjustment_date=D1,
            created_by=9,
        )
    )

    assert result == (
        first,
        second,
    )

    assert (
        wrapper.await_count
        == 2
    )

    assert (
        wrapper.await_args_list[0]
        .kwargs["tax_calculation_id"]
        == 41
    )

    assert (
        wrapper.await_args_list[1]
        .kwargs["tax_calculation_id"]
        == 42
    )


def test_input_domain_error_is_wrapped_as_lifecycle_error(
    monkeypatch,
):
    db = object()

    wrapper = AsyncMock(
        side_effect=(
            InputTaxRecognitionReconciliationStateError(
                "broken input state"
            )
        )
    )

    monkeypatch.setattr(
        lifecycle,
        "reconcile_input_tax_calculation_from_active_sources",
        wrapper,
    )

    with pytest.raises(
        lifecycle.TaxRecognitionLifecycleError,
        match=(
            "INPUT VAT recognition "
            "reconciliation failed"
        ),
    ):
        asyncio.run(
            lifecycle._reconcile_input_ids(
                db,
                company_id=1,
                calculation_ids=(
                    51,
                ),
                adjustment_date=D1,
                created_by=10,
            )
        )


def test_invalid_input_invoice_line_fails_before_db(
    monkeypatch,
):
    unexpected = AsyncMock()

    monkeypatch.setattr(
        lifecycle,
        "_get_input_tax_calculation_ids",
        unexpected,
    )

    with pytest.raises(
        ValueError,
        match=(
            "invoice_line_id must be "
            "greater than zero"
        ),
    ):
        asyncio.run(
            lifecycle.reconcile_input_tax_for_invoice_line(
                object(),
                company_id=1,
                invoice_id=20,
                invoice_line_id=0,
                adjustment_date=D1,
                created_by=7,
            )
        )

    unexpected.assert_not_awaited()


def test_tax_direction_enum_input_value_is_stable():
    assert (
        TaxDirection.INPUT.value
        == "input"
    )
