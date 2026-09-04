from datetime import date
from types import SimpleNamespace

import pytest

import app.services.customer_advance_clearing_lifecycle_service as service

from app.services.customer_advance_clearing_journal_service import (
    CustomerAdvanceClearingJournalError,
)
from app.services.customer_advance_clearing_lifecycle_service import (
    CustomerAdvanceClearingLifecycleError,
    reconcile_customer_advance_clearing_lifecycle_for_invoice,
)
from app.services.customer_advance_clearing_persistence_service import (
    CustomerAdvanceClearingPersistenceError,
)
from app.services.customer_advance_clearing_reconciliation_service import (
    CustomerAdvanceClearingReconciliationError,
)


D1 = date(
    2026,
    9,
    4,
)


class FakeDb:
    pass


def event(
    *,
    event_id,
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        reversal_of_id=reversal_of_id,
    )


def result(
    *events,
):
    return SimpleNamespace(
        created_events=tuple(
            events
        )
    )


@pytest.mark.parametrize(
    "kwargs",
    (
        {
            "company_id": 0,
            "invoice_id": 10,
            "adjustment_date": D1,
            "created_by": 1,
        },
        {
            "company_id": 1,
            "invoice_id": 0,
            "adjustment_date": D1,
            "created_by": 1,
        },
        {
            "company_id": 1,
            "invoice_id": 10,
            "adjustment_date": "2026-09-04",
            "created_by": 1,
        },
        {
            "company_id": 1,
            "invoice_id": 10,
            "adjustment_date": D1,
            "created_by": 0,
        },
    ),
)
@pytest.mark.asyncio
async def test_invalid_context_rejected_before_reconciliation(
    monkeypatch,
    kwargs,
):
    async def fail_reconcile(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "reconciliation must not run"
        )

    monkeypatch.setattr(
        service,
        "reconcile_customer_advance_clearing_for_invoice",
        fail_reconcile,
    )

    with pytest.raises(
        ValueError
    ):
        await (
            reconcile_customer_advance_clearing_lifecycle_for_invoice(
                FakeDb(),
                **kwargs,
            )
        )


@pytest.mark.asyncio
async def test_lifecycle_forwards_invoice_context(
    monkeypatch,
):
    expected = result()

    seen = {}

    async def fake_reconcile(
        db,
        *,
        company_id,
        invoice_id,
        adjustment_date,
        created_by,
    ):
        seen.update(
            {
                "db": db,
                "company_id": company_id,
                "invoice_id": invoice_id,
                "adjustment_date": adjustment_date,
                "created_by": created_by,
            }
        )

        return expected

    monkeypatch.setattr(
        service,
        "reconcile_customer_advance_clearing_for_invoice",
        fake_reconcile,
    )

    actual = (
        await reconcile_customer_advance_clearing_lifecycle_for_invoice(
            FakeDb(),
            company_id=1,
            invoice_id=50,
            adjustment_date=D1,
            created_by=7,
        )
    )

    assert actual is expected

    assert seen[
        "company_id"
    ] == 1

    assert seen[
        "invoice_id"
    ] == 50

    assert seen[
        "adjustment_date"
    ] == D1

    assert seen[
        "created_by"
    ] == 7


@pytest.mark.asyncio
async def test_created_events_are_journaled_in_exact_order(
    monkeypatch,
):
    events = (
        event(
            event_id=10,
        ),
        event(
            event_id=11,
            reversal_of_id=10,
        ),
        event(
            event_id=12,
        ),
    )

    expected = result(
        *events
    )

    async def fake_reconcile(
        *args,
        **kwargs,
    ):
        return expected

    calls = []

    async def fake_original(
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

    async def fake_reversal(
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
        service,
        "reconcile_customer_advance_clearing_for_invoice",
        fake_reconcile,
    )

    monkeypatch.setattr(
        service,
        "generate_and_post_customer_advance_clearing_journal_entry",
        fake_original,
    )

    monkeypatch.setattr(
        service,
        "reverse_customer_advance_clearing_journal_entry",
        fake_reversal,
    )

    actual = (
        await reconcile_customer_advance_clearing_lifecycle_for_invoice(
            FakeDb(),
            company_id=1,
            invoice_id=50,
            adjustment_date=D1,
            created_by=7,
        )
    )

    assert actual is expected

    assert calls == [
        (
            "original",
            10,
            7,
        ),
        (
            "reversal",
            11,
            7,
        ),
        (
            "original",
            12,
            7,
        ),
    ]


@pytest.mark.asyncio
async def test_reconciliation_error_becomes_lifecycle_error(
    monkeypatch,
):
    async def fail(
        *args,
        **kwargs,
    ):
        raise CustomerAdvanceClearingReconciliationError(
            "bad reconciliation"
        )

    monkeypatch.setattr(
        service,
        "reconcile_customer_advance_clearing_for_invoice",
        fail,
    )

    with pytest.raises(
        CustomerAdvanceClearingLifecycleError,
        match="reconciliation failed",
    ):
        await (
            reconcile_customer_advance_clearing_lifecycle_for_invoice(
                FakeDb(),
                company_id=1,
                invoice_id=50,
                adjustment_date=D1,
                created_by=7,
            )
        )


@pytest.mark.asyncio
async def test_persistence_error_becomes_lifecycle_error(
    monkeypatch,
):
    async def fail(
        *args,
        **kwargs,
    ):
        raise CustomerAdvanceClearingPersistenceError(
            "bad persistence"
        )

    monkeypatch.setattr(
        service,
        "reconcile_customer_advance_clearing_for_invoice",
        fail,
    )

    with pytest.raises(
        CustomerAdvanceClearingLifecycleError,
        match="reconciliation failed",
    ):
        await (
            reconcile_customer_advance_clearing_lifecycle_for_invoice(
                FakeDb(),
                company_id=1,
                invoice_id=50,
                adjustment_date=D1,
                created_by=7,
            )
        )


@pytest.mark.asyncio
async def test_journal_error_becomes_lifecycle_error(
    monkeypatch,
):
    async def fake_reconcile(
        *args,
        **kwargs,
    ):
        return result(
            event(
                event_id=10
            )
        )

    async def fail_journal(
        *args,
        **kwargs,
    ):
        raise CustomerAdvanceClearingJournalError(
            "bad journal"
        )

    monkeypatch.setattr(
        service,
        "reconcile_customer_advance_clearing_for_invoice",
        fake_reconcile,
    )

    monkeypatch.setattr(
        service,
        "generate_and_post_customer_advance_clearing_journal_entry",
        fail_journal,
    )

    with pytest.raises(
        CustomerAdvanceClearingLifecycleError,
        match="accounting failed",
    ):
        await (
            reconcile_customer_advance_clearing_lifecycle_for_invoice(
                FakeDb(),
                company_id=1,
                invoice_id=50,
                adjustment_date=D1,
                created_by=7,
            )
        )


def test_lifecycle_does_not_own_transaction():
    import inspect

    source = inspect.getsource(
        reconcile_customer_advance_clearing_lifecycle_for_invoice
    )

    assert "commit(" not in source
    assert "rollback(" not in source
