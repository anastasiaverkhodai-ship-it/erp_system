from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.tax_recognition_lifecycle_service as service
from app.services.tax_recognition_journal_service import (
    TaxRecognitionJournalError,
)
from app.services.tax_recognition_lifecycle_service import (
    TaxRecognitionLifecycleError,
)


@pytest.mark.asyncio
async def test_input_created_events_are_posted_in_result_order(
    monkeypatch,
):
    db = object()

    original = SimpleNamespace(
        id=101,
        reversal_of_id=None,
    )
    reversal = SimpleNamespace(
        id=102,
        reversal_of_id=101,
    )

    result = SimpleNamespace(
        created_events=(
            original,
            reversal,
        )
    )

    reconcile = AsyncMock(
        return_value=result
    )
    post_original = AsyncMock()
    post_reversal = AsyncMock()

    monkeypatch.setattr(
        service,
        "reconcile_input_tax_calculation_from_active_sources",
        reconcile,
    )
    monkeypatch.setattr(
        service,
        "generate_and_post_input_vat_recognition_journal_entry",
        post_original,
    )
    monkeypatch.setattr(
        service,
        "reverse_input_vat_recognition_journal_entry",
        post_reversal,
    )

    results = await service._reconcile_input_ids(
        db,
        company_id=1,
        calculation_ids=(
            301,
        ),
        adjustment_date=(
            __import__("datetime").date(
                2026,
                8,
                15,
            )
        ),
        created_by=99,
    )

    assert results == (
        result,
    )

    post_original.assert_awaited_once_with(
        db,
        event=original,
        created_by=99,
    )
    post_reversal.assert_awaited_once_with(
        db,
        reversal_event=reversal,
        reversed_by=99,
    )


@pytest.mark.asyncio
async def test_input_no_created_events_produces_no_gl_calls(
    monkeypatch,
):
    db = object()

    result = SimpleNamespace(
        created_events=()
    )

    monkeypatch.setattr(
        service,
        "reconcile_input_tax_calculation_from_active_sources",
        AsyncMock(
            return_value=result
        ),
    )

    post_original = AsyncMock()
    post_reversal = AsyncMock()

    monkeypatch.setattr(
        service,
        "generate_and_post_input_vat_recognition_journal_entry",
        post_original,
    )
    monkeypatch.setattr(
        service,
        "reverse_input_vat_recognition_journal_entry",
        post_reversal,
    )

    await service._reconcile_input_ids(
        db,
        company_id=1,
        calculation_ids=(
            301,
        ),
        adjustment_date=(
            __import__("datetime").date(
                2026,
                8,
                15,
            )
        ),
        created_by=99,
    )

    post_original.assert_not_awaited()
    post_reversal.assert_not_awaited()


@pytest.mark.asyncio
async def test_input_journal_failure_is_wrapped_by_lifecycle(
    monkeypatch,
):
    db = object()

    event = SimpleNamespace(
        id=101,
        reversal_of_id=None,
    )

    monkeypatch.setattr(
        service,
        "reconcile_input_tax_calculation_from_active_sources",
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
        "generate_and_post_input_vat_recognition_journal_entry",
        AsyncMock(
            side_effect=(
                TaxRecognitionJournalError(
                    "journal failed"
                )
            )
        ),
    )

    with pytest.raises(
        TaxRecognitionLifecycleError,
        match=(
            "INPUT VAT recognition "
            "journal posting failed"
        ),
    ):
        await service._reconcile_input_ids(
            db,
            company_id=1,
            calculation_ids=(
                301,
            ),
            adjustment_date=(
                __import__("datetime").date(
                    2026,
                    8,
                    15,
                )
            ),
            created_by=99,
        )
