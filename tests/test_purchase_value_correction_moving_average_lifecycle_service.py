from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.purchase_value_correction_moving_average_lifecycle_service as lifecycle_module
from app.services.purchase_value_correction_moving_average_lifecycle_service import (
    post_created_purchase_value_correction_moving_average_peer_journals,
)


@pytest.mark.asyncio
async def test_peer_lifecycle_consumes_original_reversal_replacement_in_order(
    monkeypatch,
):
    original = SimpleNamespace(
        id=1,
        reversal_of_id=None,
    )
    reversal = SimpleNamespace(
        id=2,
        reversal_of_id=1,
    )
    replacement = SimpleNamespace(
        id=3,
        reversal_of_id=None,
    )

    result = SimpleNamespace(
        created_events=(
            original,
            reversal,
            replacement,
        )
    )

    monkeypatch.setattr(
        lifecycle_module,
        "PurchaseValueCorrectionMovingAveragePeerReconciliationResult",
        SimpleNamespace,
    )

    calls = []

    async def generate(
        db,
        *,
        event,
        created_by,
    ):
        calls.append(
            (
                "original",
                event.id,
                created_by,
            )
        )
        return SimpleNamespace(
            id=100 + event.id
        )

    async def reverse(
        db,
        *,
        reversal_event,
        reversed_by,
    ):
        calls.append(
            (
                "reversal",
                reversal_event.id,
                reversed_by,
            )
        )
        return SimpleNamespace(
            id=200 + reversal_event.id
        )

    monkeypatch.setattr(
        lifecycle_module,
        "generate_and_post_purchase_value_correction_moving_average_journal_entry",
        generate,
    )
    monkeypatch.setattr(
        lifecycle_module,
        "reverse_purchase_value_correction_moving_average_journal_entry",
        reverse,
    )

    journals = (
        await post_created_purchase_value_correction_moving_average_peer_journals(
            object(),
            result=result,
            created_by=9,
        )
    )

    assert calls == [
        (
            "original",
            1,
            9,
        ),
        (
            "reversal",
            2,
            9,
        ),
        (
            "original",
            3,
            9,
        ),
    ]

    assert len(
        journals
    ) == 3


@pytest.mark.asyncio
async def test_peer_orchestration_reconcile_then_post(
    monkeypatch,
):
    reconciliation_result = SimpleNamespace(
        created_events=()
    )

    reconcile_mock = AsyncMock(
        return_value=reconciliation_result
    )
    post_mock = AsyncMock(
        return_value=()
    )

    monkeypatch.setattr(
        lifecycle_module,
        "reconcile_purchase_value_correction_moving_average_peers",
        reconcile_mock,
    )
    monkeypatch.setattr(
        lifecycle_module,
        "post_created_purchase_value_correction_moving_average_peer_journals",
        post_mock,
    )

    db = object()

    result = (
        await lifecycle_module.reconcile_and_post_purchase_value_correction_moving_average_peers(
            db,
            company_id=1,
            anchor_allocation_event_id=10,
            adjustment_date=date(2026, 9, 10),
            created_by=9,
        )
    )

    assert result is reconciliation_result

    reconcile_mock.assert_awaited_once_with(
        db,
        company_id=1,
        anchor_allocation_event_id=10,
        adjustment_date=date(2026, 9, 10),
        created_by=9,
    )

    post_mock.assert_awaited_once_with(
        db,
        result=reconciliation_result,
        created_by=9,
    )
