import asyncio
from functools import wraps

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)
import app.services.purchase_return_recognition_lifecycle_service as service


def _run_async_test(func):
    """
    Run an async unit-test body without requiring a pytest async plugin.
    """
    @wraps(func)
    def wrapper(
        *args,
        **kwargs,
    ):
        return asyncio.run(
            func(
                *args,
                **kwargs,
            )
        )

    return wrapper


class _Session:
    async def commit(
        self,
    ):
        raise AssertionError(
            "lifecycle must not commit"
        )

    async def rollback(
        self,
    ):
        raise AssertionError(
            "lifecycle must not rollback"
        )


def _event(
    *,
    event_id,
    reversal_of_id=None,
    base="5.00",
):
    return PurchaseReturnRecognitionEvent(
        id=event_id,
        company_id=1,
        trade_return_event_id=20,
        invoice_fulfillment_allocation_id=30,
        recognition_date=date(
            2026,
            9,
            5,
        ),
        returned_quantity=Decimal("1"),
        returned_base_amount=Decimal(
            base
        ),
        returned_gross_amount=Decimal("6.00"),
        returned_tax_amount=Decimal("1.00"),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=reversal_of_id,
    )


@_run_async_test
async def test_lifecycle_consumes_created_events_in_exact_order(
    monkeypatch,
):
    db = _Session()

    original = _event(
        event_id=10,
    )

    reversal = _event(
        event_id=11,
        reversal_of_id=10,
    )

    replacement = _event(
        event_id=12,
        base="3.00",
    )

    result = SimpleNamespace(
        created_events=(
            original,
            reversal,
            replacement,
        )
    )

    async def reconcile(
        db,
        *,
        company_id,
        fulfillment_id,
        fulfillment_line_id,
        created_by,
        adjustment_date,
    ):
        assert company_id == 1
        assert fulfillment_id == 2
        assert fulfillment_line_id == 3
        assert created_by == 4
        assert adjustment_date == date(
            2026,
            9,
            5,
        )

        return result

    calls = []

    async def post_original(
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

    async def post_reversal(
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
        "reconcile_purchase_return_recognition_for_fulfillment_line",
        reconcile,
    )

    monkeypatch.setattr(
        service,
        "generate_and_post_purchase_return_recognition_journal_entry",
        post_original,
    )

    monkeypatch.setattr(
        service,
        "reverse_purchase_return_recognition_journal_entry",
        post_reversal,
    )

    actual = await (
        service
        .reconcile_purchase_return_recognition_lifecycle_for_fulfillment_line(
            db,
            company_id=1,
            fulfillment_id=2,
            fulfillment_line_id=3,
            adjustment_date=date(
                2026,
                9,
                5,
            ),
            created_by=4,
        )
    )

    assert actual is result

    assert calls == [
        (
            "original",
            10,
            4,
        ),
        (
            "reversal",
            11,
            4,
        ),
        (
            "original",
            12,
            4,
        ),
    ]


@_run_async_test
async def test_lifecycle_wraps_reconciliation_error(
    monkeypatch,
):
    async def reconcile(
        *args,
        **kwargs,
    ):
        raise (
            service
            .PurchaseReturnRecognitionReconciliationError(
                "bad reconciliation"
            )
        )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_recognition_for_fulfillment_line",
        reconcile,
    )

    with pytest.raises(
        service.PurchaseReturnRecognitionLifecycleError,
        match="reconciliation failed",
    ):
        await (
            service
            .reconcile_purchase_return_recognition_lifecycle_for_fulfillment_line(
                _Session(),
                company_id=1,
                fulfillment_id=2,
                fulfillment_line_id=3,
                adjustment_date=date(
                    2026,
                    9,
                    5,
                ),
                created_by=4,
            )
        )


@_run_async_test
async def test_lifecycle_wraps_journal_error(
    monkeypatch,
):
    result = SimpleNamespace(
        created_events=(
            _event(
                event_id=10,
            ),
        )
    )

    async def reconcile(
        *args,
        **kwargs,
    ):
        return result

    async def fail_post(
        *args,
        **kwargs,
    ):
        raise (
            service
            .PurchaseReturnRecognitionJournalError(
                "bad journal"
            )
        )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_recognition_for_fulfillment_line",
        reconcile,
    )

    monkeypatch.setattr(
        service,
        "generate_and_post_purchase_return_recognition_journal_entry",
        fail_post,
    )

    with pytest.raises(
        service.PurchaseReturnRecognitionLifecycleError,
        match="journal posting failed",
    ):
        await (
            service
            .reconcile_purchase_return_recognition_lifecycle_for_fulfillment_line(
                _Session(),
                company_id=1,
                fulfillment_id=2,
                fulfillment_line_id=3,
                adjustment_date=date(
                    2026,
                    9,
                    5,
                ),
                created_by=4,
            )
        )


@pytest.mark.parametrize(
    (
        "company_id",
        "fulfillment_id",
        "fulfillment_line_id",
        "created_by",
    ),
    [
        (
            0,
            2,
            3,
            4,
        ),
        (
            1,
            0,
            3,
            4,
        ),
        (
            1,
            2,
            0,
            4,
        ),
        (
            1,
            2,
            3,
            0,
        ),
    ],
)
@_run_async_test
async def test_lifecycle_validates_positive_context_ids(
    company_id,
    fulfillment_id,
    fulfillment_line_id,
    created_by,
):
    with pytest.raises(
        ValueError
    ):
        await (
            service
            .reconcile_purchase_return_recognition_lifecycle_for_fulfillment_line(
                _Session(),
                company_id=company_id,
                fulfillment_id=fulfillment_id,
                fulfillment_line_id=fulfillment_line_id,
                adjustment_date=date(
                    2026,
                    9,
                    5,
                ),
                created_by=created_by,
            )
        )


@_run_async_test
async def test_lifecycle_validates_adjustment_date():
    with pytest.raises(
        TypeError
    ):
        await (
            service
            .reconcile_purchase_return_recognition_lifecycle_for_fulfillment_line(
                _Session(),
                company_id=1,
                fulfillment_id=2,
                fulfillment_line_id=3,
                adjustment_date="2026-09-05",
                created_by=4,
            )
        )
