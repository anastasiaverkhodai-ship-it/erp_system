from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.purchase_value_correction_fifo_lifecycle_service as lifecycle_module
from app.services.purchase_value_correction_fifo_impact_reconciliation_service import (
    PurchaseValueCorrectionFifoImpactReconciliationResult,
)
from app.services.purchase_value_correction_fifo_lifecycle_service import (
    post_created_purchase_value_correction_fifo_impact_journals,
    reconcile_and_post_purchase_value_correction_fifo_impacts_for_fulfillment_line,
)


def reconciliation_result(
    *events,
):
    return (
        PurchaseValueCorrectionFifoImpactReconciliationResult(
            fulfillment_line_id=100,
            stock_lot_id=200,
            active_allocation_peer_ids=(),
            active_correction_allocation_event_ids=(),
            desired_targets=(),
            reconciliation_targets=(),
            created_events=tuple(
                events
            ),
        )
    )


@pytest.mark.asyncio
async def test_lifecycle_consumes_original_reversal_replacement_in_exact_order(
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

    calls = []

    async def fake_generate(
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
            id=1000 + event.id
        )

    async def fake_reverse(
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
            id=2000 + reversal_event.id
        )

    monkeypatch.setattr(
        lifecycle_module,
        "generate_and_post_purchase_value_correction_fifo_journal_entry",
        fake_generate,
    )

    monkeypatch.setattr(
        lifecycle_module,
        "reverse_purchase_value_correction_fifo_journal_entry",
        fake_reverse,
    )

    result = reconciliation_result(
        original,
        reversal,
        replacement,
    )

    journals = (
        await post_created_purchase_value_correction_fifo_impact_journals(
            SimpleNamespace(),
            result=result,
            created_by=77,
        )
    )

    assert calls == [
        (
            "original",
            1,
            77,
        ),
        (
            "reversal",
            2,
            77,
        ),
        (
            "original",
            3,
            77,
        ),
    ]

    assert tuple(
        journal.id
        for journal in journals
    ) == (
        1001,
        2002,
        1003,
    )


@pytest.mark.asyncio
async def test_empty_reconciliation_is_gl_noop(
    monkeypatch,
):
    generate = AsyncMock()
    reverse = AsyncMock()

    monkeypatch.setattr(
        lifecycle_module,
        "generate_and_post_purchase_value_correction_fifo_journal_entry",
        generate,
    )

    monkeypatch.setattr(
        lifecycle_module,
        "reverse_purchase_value_correction_fifo_journal_entry",
        reverse,
    )

    result = reconciliation_result()

    journals = (
        await post_created_purchase_value_correction_fifo_impact_journals(
            SimpleNamespace(),
            result=result,
            created_by=77,
        )
    )

    assert journals == ()

    generate.assert_not_awaited()
    reverse.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrapper_reconciles_then_posts_created_events(
    monkeypatch,
):
    original = SimpleNamespace(
        id=10,
        reversal_of_id=None,
    )

    result = reconciliation_result(
        original
    )

    reconcile = AsyncMock(
        return_value=result
    )

    post = AsyncMock(
        return_value=(
            SimpleNamespace(
                id=9001
            ),
        )
    )

    monkeypatch.setattr(
        lifecycle_module,
        "reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line",
        reconcile,
    )

    monkeypatch.setattr(
        lifecycle_module,
        "post_created_purchase_value_correction_fifo_impact_journals",
        post,
    )

    db = SimpleNamespace()

    returned = (
        await reconcile_and_post_purchase_value_correction_fifo_impacts_for_fulfillment_line(
            db,
            company_id=1,
            fulfillment_line_id=100,
            adjustment_date=date(
                2026,
                9,
                5,
            ),
            created_by=77,
        )
    )

    assert returned is result

    reconcile.assert_awaited_once_with(
        db,
        company_id=1,
        fulfillment_line_id=100,
        adjustment_date=date(
            2026,
            9,
            5,
        ),
        created_by=77,
    )

    post.assert_awaited_once_with(
        db,
        result=result,
        created_by=77,
    )
