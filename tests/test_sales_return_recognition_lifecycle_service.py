from datetime import date
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
)

import pytest

import app.services.sales_return_recognition_lifecycle_service as service

from app.services.sales_return_recognition_journal_service import (
    SalesReturnRecognitionJournalError,
)
from app.services.sales_return_recognition_lifecycle_service import (
    SalesReturnRecognitionLifecycleError,
    _post_created_sales_return_recognition_events,
    _validate_context,
    reconcile_sales_return_recognition_lifecycle_for_fulfillment_line,
)
from app.services.sales_return_recognition_persistence_service import (
    SalesReturnRecognitionPersistenceError,
)
from app.services.sales_return_recognition_reconciliation_service import (
    SalesReturnRecognitionReconciliationError,
)


D = date(
    2026,
    9,
    4,
)


def result_with_events(
    *events,
):
    return SimpleNamespace(
        created_events=tuple(
            events
        )
    )


def original(
    event_id,
):
    return SimpleNamespace(
        id=event_id,
        reversal_of_id=None,
    )


def reversal(
    event_id,
    original_id,
):
    return SimpleNamespace(
        id=event_id,
        reversal_of_id=original_id,
    )


def test_validate_context_accepts_valid_values():
    _validate_context(
        company_id=1,
        fulfillment_id=2,
        fulfillment_line_id=3,
        adjustment_date=D,
        created_by=4,
    )


@pytest.mark.parametrize(
    (
        "field",
        "value",
    ),
    (
        (
            "company_id",
            0,
        ),
        (
            "fulfillment_id",
            0,
        ),
        (
            "fulfillment_line_id",
            0,
        ),
        (
            "created_by",
            0,
        ),
    ),
)
def test_validate_context_rejects_nonpositive_ids(
    field,
    value,
):
    kwargs = {
        "company_id": 1,
        "fulfillment_id": 2,
        "fulfillment_line_id": 3,
        "adjustment_date": D,
        "created_by": 4,
    }

    kwargs[
        field
    ] = value

    with pytest.raises(
        ValueError,
        match="greater than zero",
    ):
        _validate_context(
            **kwargs
        )


def test_validate_context_requires_date():
    with pytest.raises(
        TypeError,
        match="adjustment_date",
    ):
        _validate_context(
            company_id=1,
            fulfillment_id=2,
            fulfillment_line_id=3,
            adjustment_date="2026-09-04",
            created_by=4,
        )


@pytest.mark.asyncio
async def test_post_helper_posts_original_event(
    monkeypatch,
):
    event = original(
        10
    )

    post = AsyncMock()

    reverse = AsyncMock()

    monkeypatch.setattr(
        service,
        "generate_and_post_sales_return_recognition_journal_entry",
        post,
    )

    monkeypatch.setattr(
        service,
        "reverse_sales_return_recognition_journal_entry",
        reverse,
    )

    await _post_created_sales_return_recognition_events(
        object(),
        result=result_with_events(
            event
        ),
        created_by=7,
    )

    post.assert_awaited_once_with(
        object(),
        event=event,
        created_by=7,
    ) if False else None

    assert (
        post.await_count
        == 1
    )

    assert (
        post.await_args.kwargs[
            "event"
        ]
        is event
    )

    assert (
        post.await_args.kwargs[
            "created_by"
        ]
        == 7
    )

    reverse.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_helper_reverses_reversal_event(
    monkeypatch,
):
    event = reversal(
        11,
        10,
    )

    post = AsyncMock()
    reverse = AsyncMock()

    monkeypatch.setattr(
        service,
        "generate_and_post_sales_return_recognition_journal_entry",
        post,
    )

    monkeypatch.setattr(
        service,
        "reverse_sales_return_recognition_journal_entry",
        reverse,
    )

    db = object()

    await _post_created_sales_return_recognition_events(
        db,
        result=result_with_events(
            event
        ),
        created_by=7,
    )

    post.assert_not_awaited()

    reverse.assert_awaited_once_with(
        db,
        reversal_event=event,
        reversed_by=7,
    )


@pytest.mark.asyncio
async def test_post_helper_preserves_exact_event_order(
    monkeypatch,
):
    first = reversal(
        11,
        10,
    )

    second = original(
        12
    )

    calls = []

    async def fake_post(
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

    monkeypatch.setattr(
        service,
        "generate_and_post_sales_return_recognition_journal_entry",
        fake_post,
    )

    monkeypatch.setattr(
        service,
        "reverse_sales_return_recognition_journal_entry",
        fake_reverse,
    )

    await _post_created_sales_return_recognition_events(
        object(),
        result=result_with_events(
            first,
            second,
        ),
        created_by=9,
    )

    assert calls == [
        (
            "reversal",
            11,
            9,
        ),
        (
            "original",
            12,
            9,
        ),
    ]


@pytest.mark.asyncio
async def test_lifecycle_reconciles_then_posts(
    monkeypatch,
):
    db = object()

    result = result_with_events(
        original(
            10
        )
    )

    reconcile = AsyncMock(
        return_value=result
    )

    post = AsyncMock()

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_recognition_for_fulfillment_line",
        reconcile,
    )

    monkeypatch.setattr(
        service,
        "_post_created_sales_return_recognition_events",
        post,
    )

    actual = (
        await reconcile_sales_return_recognition_lifecycle_for_fulfillment_line(
            db,
            company_id=1,
            fulfillment_id=20,
            fulfillment_line_id=21,
            adjustment_date=D,
            created_by=7,
        )
    )

    assert actual is result

    reconcile.assert_awaited_once_with(
        db,
        company_id=1,
        fulfillment_id=20,
        fulfillment_line_id=21,
        created_by=7,
        adjustment_date=D,
    )

    post.assert_awaited_once_with(
        db,
        result=result,
        created_by=7,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    (
        SalesReturnRecognitionPersistenceError(
            "persistence"
        ),
        SalesReturnRecognitionReconciliationError(
            "reconciliation"
        ),
    ),
)
async def test_lifecycle_wraps_reconciliation_failures(
    monkeypatch,
    error,
):
    reconcile = AsyncMock(
        side_effect=error
    )

    post = AsyncMock()

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_recognition_for_fulfillment_line",
        reconcile,
    )

    monkeypatch.setattr(
        service,
        "_post_created_sales_return_recognition_events",
        post,
    )

    with pytest.raises(
        SalesReturnRecognitionLifecycleError,
        match="reconciliation failed",
    ):
        await (
            reconcile_sales_return_recognition_lifecycle_for_fulfillment_line(
                object(),
                company_id=1,
                fulfillment_id=20,
                fulfillment_line_id=21,
                adjustment_date=D,
                created_by=7,
            )
        )

    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_lifecycle_wraps_journal_failure(
    monkeypatch,
):
    result = result_with_events(
        original(
            10
        )
    )

    reconcile = AsyncMock(
        return_value=result
    )

    post = AsyncMock(
        side_effect=(
            SalesReturnRecognitionJournalError(
                "journal"
            )
        )
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_recognition_for_fulfillment_line",
        reconcile,
    )

    monkeypatch.setattr(
        service,
        "_post_created_sales_return_recognition_events",
        post,
    )

    with pytest.raises(
        SalesReturnRecognitionLifecycleError,
        match="journal posting failed",
    ):
        await (
            reconcile_sales_return_recognition_lifecycle_for_fulfillment_line(
                object(),
                company_id=1,
                fulfillment_id=20,
                fulfillment_line_id=21,
                adjustment_date=D,
                created_by=7,
            )
        )


@pytest.mark.asyncio
async def test_empty_created_events_is_valid_noop(
    monkeypatch,
):
    result = result_with_events()

    reconcile = AsyncMock(
        return_value=result
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_recognition_for_fulfillment_line",
        reconcile,
    )

    post_original = AsyncMock()
    reverse = AsyncMock()

    monkeypatch.setattr(
        service,
        "generate_and_post_sales_return_recognition_journal_entry",
        post_original,
    )

    monkeypatch.setattr(
        service,
        "reverse_sales_return_recognition_journal_entry",
        reverse,
    )

    actual = (
        await reconcile_sales_return_recognition_lifecycle_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_id=20,
            fulfillment_line_id=21,
            adjustment_date=D,
            created_by=7,
        )
    )

    assert actual is result
    post_original.assert_not_awaited()
    reverse.assert_not_awaited()
