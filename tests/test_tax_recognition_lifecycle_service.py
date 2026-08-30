from datetime import date
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

import app.services.tax_recognition_lifecycle_service as service

from app.services.tax_recognition_lifecycle_service import (
    TaxRecognitionLifecycleError,
    reconcile_output_tax_for_invoice,
    reconcile_output_tax_for_invoice_line,
)
from app.services.tax_recognition_persistence_service import (
    TaxRecognitionDataIntegrityError,
)


def _db_with_ids(
    *ids: int,
):
    scalars = Mock()
    scalars.all.return_value = list(
        ids
    )

    result = Mock()
    result.scalars.return_value = (
        scalars
    )

    db = Mock()
    db.execute = AsyncMock(
        return_value=result
    )

    return db


@pytest.mark.asyncio
async def test_invoice_line_without_output_tax_is_noop(
    monkeypatch,
):
    db = _db_with_ids()

    reconcile = AsyncMock()

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_calculation_from_active_sources",
        reconcile,
    )

    result = (
        await reconcile_output_tax_for_invoice_line(
            db,
            company_id=1,
            invoice_id=10,
            invoice_line_id=20,
            adjustment_date=date(
                2026,
                8,
                30,
            ),
            created_by=99,
        )
    )

    assert result == ()

    reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_invoice_line_reconciles_output_calculation(
    monkeypatch,
):
    db = _db_with_ids(
        101
    )

    expected = object()

    reconcile = AsyncMock(
        return_value=expected
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_calculation_from_active_sources",
        reconcile,
    )

    result = (
        await reconcile_output_tax_for_invoice_line(
            db,
            company_id=1,
            invoice_id=10,
            invoice_line_id=20,
            adjustment_date=date(
                2026,
                8,
                30,
            ),
            created_by=99,
        )
    )

    assert result == (
        expected,
    )

    reconcile.assert_awaited_once_with(
        db,
        company_id=1,
        tax_calculation_id=101,
        adjustment_date=date(
            2026,
            8,
            30,
        ),
        created_by=99,
    )


@pytest.mark.asyncio
async def test_invoice_reconciles_all_output_tax_lines(
    monkeypatch,
):
    db = _db_with_ids(
        101,
        102,
    )

    first = object()
    second = object()

    reconcile = AsyncMock(
        side_effect=[
            first,
            second,
        ]
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_calculation_from_active_sources",
        reconcile,
    )

    result = (
        await reconcile_output_tax_for_invoice(
            db,
            company_id=1,
            invoice_id=10,
            adjustment_date=date(
                2026,
                8,
                30,
            ),
            created_by=99,
        )
    )

    assert result == (
        first,
        second,
    )

    assert reconcile.await_count == 2

    assert [
        call.kwargs[
            "tax_calculation_id"
        ]
        for call
        in reconcile.await_args_list
    ] == [
        101,
        102,
    ]


@pytest.mark.asyncio
async def test_recognition_domain_error_is_wrapped(
    monkeypatch,
):
    db = _db_with_ids(
        101
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_calculation_from_active_sources",
        AsyncMock(
            side_effect=(
                TaxRecognitionDataIntegrityError(
                    "broken recognition ledger"
                )
            )
        ),
    )

    with pytest.raises(
        TaxRecognitionLifecycleError,
        match=(
            "broken recognition ledger"
        ),
    ):
        await reconcile_output_tax_for_invoice(
            db,
            company_id=1,
            invoice_id=10,
            adjustment_date=date(
                2026,
                8,
                30,
            ),
            created_by=99,
        )


@pytest.mark.asyncio
async def test_invalid_context_fails_before_database():
    db = _db_with_ids(
        101
    )

    with pytest.raises(
        ValueError
    ):
        await reconcile_output_tax_for_invoice(
            db,
            company_id=0,
            invoice_id=10,
            adjustment_date=date(
                2026,
                8,
                30,
            ),
            created_by=99,
        )

    db.execute.assert_not_awaited()
