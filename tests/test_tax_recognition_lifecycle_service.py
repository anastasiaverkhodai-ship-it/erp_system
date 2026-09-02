from datetime import date
from types import SimpleNamespace
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

    expected = SimpleNamespace(
        created_events=()
    )

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

    first = SimpleNamespace(
        created_events=()
    )
    second = SimpleNamespace(
        created_events=()
    )

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


@pytest.mark.asyncio
async def test_original_created_event_posts_vat_journal(
    monkeypatch,
):
    db = _db_with_ids(
        101
    )

    event = SimpleNamespace(
        reversal_of_id=None
    )
    expected = SimpleNamespace(
        created_events=(
            event,
        )
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_calculation_from_active_sources",
        AsyncMock(
            return_value=expected
        ),
    )

    generate = AsyncMock()
    reverse = AsyncMock()

    monkeypatch.setattr(
        service,
        "generate_and_post_output_vat_recognition_journal_entry",
        generate,
    )
    monkeypatch.setattr(
        service,
        "reverse_output_vat_recognition_journal_entry",
        reverse,
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

    generate.assert_awaited_once_with(
        db,
        event=event,
        created_by=99,
    )
    reverse.assert_not_awaited()


@pytest.mark.asyncio
async def test_reversal_then_replacement_preserves_event_order(
    monkeypatch,
):
    db = _db_with_ids(
        101
    )

    reversal_event = SimpleNamespace(
        reversal_of_id=5
    )
    replacement_event = SimpleNamespace(
        reversal_of_id=None
    )

    expected = SimpleNamespace(
        created_events=(
            reversal_event,
            replacement_event,
        )
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_calculation_from_active_sources",
        AsyncMock(
            return_value=expected
        ),
    )

    calls = []

    async def fake_reverse(
        db_arg,
        *,
        reversal_event,
        reversed_by,
    ):
        assert db_arg is db
        calls.append(
            (
                "reverse",
                reversal_event,
                reversed_by,
            )
        )

    async def fake_generate(
        db_arg,
        *,
        event,
        created_by,
    ):
        assert db_arg is db
        calls.append(
            (
                "generate",
                event,
                created_by,
            )
        )

    monkeypatch.setattr(
        service,
        "reverse_output_vat_recognition_journal_entry",
        fake_reverse,
    )
    monkeypatch.setattr(
        service,
        "generate_and_post_output_vat_recognition_journal_entry",
        fake_generate,
    )

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

    assert calls == [
        (
            "reverse",
            reversal_event,
            99,
        ),
        (
            "generate",
            replacement_event,
            99,
        ),
    ]


@pytest.mark.asyncio
async def test_vat_journal_error_is_wrapped(
    monkeypatch,
):
    db = _db_with_ids(
        101
    )

    event = SimpleNamespace(
        reversal_of_id=None
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_calculation_from_active_sources",
        AsyncMock(
            return_value=SimpleNamespace(
                created_events=(
                    event,
                )
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "generate_and_post_output_vat_recognition_journal_entry",
        AsyncMock(
            side_effect=(
                service.TaxRecognitionJournalError(
                    "broken VAT journal"
                )
            )
        ),
    )

    with pytest.raises(
        TaxRecognitionLifecycleError,
        match="broken VAT journal",
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

# VAT_ADVANCE_BRIDGE_AUTOUSE_LIFECYCLE_STUB
import pytest as _bridge_pytest
from unittest.mock import AsyncMock as _BridgeAsyncMock
import app.services.tax_recognition_lifecycle_service as _bridge_tax_lifecycle


@_bridge_pytest.fixture(autouse=True)
def _stub_vat_advance_bridge_lifecycle(
    monkeypatch,
):
    stub = _BridgeAsyncMock(
        return_value=None
    )

    monkeypatch.setattr(
        _bridge_tax_lifecycle,
        "reconcile_vat_advance_bridge_lifecycle_for_tax_calculation",
        stub,
    )

    return stub
