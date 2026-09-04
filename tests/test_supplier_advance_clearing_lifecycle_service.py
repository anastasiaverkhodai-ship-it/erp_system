from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import inspect
import re

import pytest

import app.services.invoice_fulfillment_allocation_service as fulfillment_service
import app.services.payment_settlement_service as payment_service
import app.services.supplier_advance_clearing_lifecycle_service as service

from app.services.supplier_advance_clearing_journal_service import (
    SupplierAdvanceClearingJournalError,
)
from app.services.supplier_advance_clearing_lifecycle_service import (
    SupplierAdvanceClearingLifecycleError,
    _post_created_supplier_advance_clearing_events,
    reconcile_supplier_advance_clearing_lifecycle_for_invoice,
)
from app.services.supplier_advance_clearing_persistence_service import (
    SupplierAdvanceClearingPersistenceError,
)
from app.services.supplier_advance_clearing_reconciliation_service import (
    SupplierAdvanceClearingReconciliationError,
)


D1 = date(
    2026,
    9,
    1,
)


def event(
    event_id,
    *,
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        reversal_of_id=reversal_of_id,
    )


@pytest.mark.asyncio
async def test_created_events_are_posted_in_exact_persistence_order(
    monkeypatch,
):
    calls = []

    original_1 = event(
        10
    )

    reversal = event(
        11,
        reversal_of_id=10,
    )

    replacement = event(
        12
    )

    result = SimpleNamespace(
        created_events=(
            original_1,
            reversal,
            replacement,
        )
    )

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
        "generate_and_post_supplier_advance_clearing_journal_entry",
        post_original,
    )

    monkeypatch.setattr(
        service,
        "reverse_supplier_advance_clearing_journal_entry",
        post_reversal,
    )

    await (
        _post_created_supplier_advance_clearing_events(
            object(),
            result=result,
            created_by=7,
        )
    )

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
async def test_lifecycle_reconciles_then_posts(
    monkeypatch,
):
    result = SimpleNamespace(
        created_events=()
    )

    reconcile = AsyncMock(
        return_value=result
    )

    post = AsyncMock(
        return_value=None
    )

    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_for_invoice",
        reconcile,
    )

    monkeypatch.setattr(
        service,
        "_post_created_supplier_advance_clearing_events",
        post,
    )

    db = object()

    actual = (
        await reconcile_supplier_advance_clearing_lifecycle_for_invoice(
            db,
            company_id=1,
            invoice_id=2,
            adjustment_date=D1,
            created_by=3,
        )
    )

    assert actual is result

    reconcile.assert_awaited_once_with(
        db,
        company_id=1,
        invoice_id=2,
        adjustment_date=D1,
        created_by=3,
    )

    post.assert_awaited_once_with(
        db,
        result=result,
        created_by=3,
    )


@pytest.mark.asyncio
async def test_reconciliation_error_is_wrapped(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_for_invoice",
        AsyncMock(
            side_effect=(
                SupplierAdvanceClearingReconciliationError(
                    "bad reconciliation"
                )
            )
        ),
    )

    with pytest.raises(
        SupplierAdvanceClearingLifecycleError,
        match="reconciliation failed",
    ):
        await reconcile_supplier_advance_clearing_lifecycle_for_invoice(
            object(),
            company_id=1,
            invoice_id=2,
            adjustment_date=D1,
            created_by=3,
        )


@pytest.mark.asyncio
async def test_persistence_error_is_wrapped(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_for_invoice",
        AsyncMock(
            side_effect=(
                SupplierAdvanceClearingPersistenceError(
                    "bad persistence"
                )
            )
        ),
    )

    with pytest.raises(
        SupplierAdvanceClearingLifecycleError,
        match="reconciliation failed",
    ):
        await reconcile_supplier_advance_clearing_lifecycle_for_invoice(
            object(),
            company_id=1,
            invoice_id=2,
            adjustment_date=D1,
            created_by=3,
        )


@pytest.mark.asyncio
async def test_journal_error_is_wrapped(
    monkeypatch,
):
    result = SimpleNamespace(
        created_events=()
    )

    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_for_invoice",
        AsyncMock(
            return_value=result
        ),
    )

    monkeypatch.setattr(
        service,
        "_post_created_supplier_advance_clearing_events",
        AsyncMock(
            side_effect=(
                SupplierAdvanceClearingJournalError(
                    "bad journal"
                )
            )
        ),
    )

    with pytest.raises(
        SupplierAdvanceClearingLifecycleError,
        match="journal posting failed",
    ):
        await reconcile_supplier_advance_clearing_lifecycle_for_invoice(
            object(),
            company_id=1,
            invoice_id=2,
            adjustment_date=D1,
            created_by=3,
        )


@pytest.mark.parametrize(
    (
        "kwargs",
        "message",
    ),
    [
        (
            {
                "company_id": 0,
                "invoice_id": 2,
                "adjustment_date": D1,
                "created_by": 3,
            },
            "company_id",
        ),
        (
            {
                "company_id": 1,
                "invoice_id": 0,
                "adjustment_date": D1,
                "created_by": 3,
            },
            "invoice_id",
        ),
        (
            {
                "company_id": 1,
                "invoice_id": 2,
                "adjustment_date": "2026-09-01",
                "created_by": 3,
            },
            "adjustment_date",
        ),
        (
            {
                "company_id": 1,
                "invoice_id": 2,
                "adjustment_date": D1,
                "created_by": 0,
            },
            "created_by",
        ),
    ],
)
@pytest.mark.asyncio
async def test_lifecycle_validates_context(
    kwargs,
    message,
):
    with pytest.raises(
        ValueError,
        match=message,
    ):
        await reconcile_supplier_advance_clearing_lifecycle_for_invoice(
            object(),
            **kwargs,
        )


def test_payment_create_routes_both_sides_to_clearing_lifecycles():
    import inspect
    import app.services.payment_settlement_service as payment_service

    source = inspect.getsource(
        payment_service
        .create_payment_settlement_allocation
    )

    assert (
        "generate_and_post_settlement_journal_entry("
        not in source
    )

    assert (
        "reconcile_customer_advance_"
        "clearing_lifecycle_for_invoice("
        in source
    )

    assert (
        "reconcile_supplier_advance_"
        "clearing_lifecycle_for_invoice("
        in source
    )


def test_payment_reverse_routes_both_sides_to_clearing_lifecycles():
    import inspect
    import app.services.payment_settlement_service as payment_service

    source = inspect.getsource(
        payment_service
        .reverse_payment_settlement_allocation
    )

    assert (
        "reverse_settlement_journal_entry("
        not in source
    )

    assert (
        "reconcile_customer_advance_"
        "clearing_lifecycle_for_invoice("
        in source
    )

    assert (
        "reconcile_supplier_advance_"
        "clearing_lifecycle_for_invoice("
        in source
    )

    assert (
        source.index(
            "PaymentSettlementAllocationStatus.REVERSED"
        )
        < source.index(
            "reconcile_customer_advance_"
            "clearing_lifecycle_for_invoice("
        )
    )


def test_fulfillment_create_runs_supplier_after_input_vat_bridge():
    source = inspect.getsource(
        fulfillment_service.create_invoice_fulfillment_allocation
    )

    assert (
        "TradeDirection.PURCHASE"
        in source
    )

    bridge_index = source.index(
        "reconcile_input_vat_fulfillment_bridge_lifecycle_for_invoice_line("
    )

    supplier_index = source.index(
        "reconcile_supplier_advance_clearing_lifecycle_for_invoice("
    )

    assert (
        bridge_index
        < supplier_index
    )


def test_fulfillment_reverse_locks_invoice_and_runs_supplier_last():
    source = inspect.getsource(
        fulfillment_service.reverse_invoice_fulfillment_allocation
    )

    invoice_lock_index = source.index(
        "invoice = await _get_locked_invoice("
    )

    allocation_lock_index = source.index(
        "result = await db.execute("
    )

    assert (
        invoice_lock_index
        < allocation_lock_index
    )

    bridge_index = source.index(
        "reconcile_input_vat_fulfillment_bridge_lifecycle_for_invoice_line("
    )

    supplier_index = source.index(
        "reconcile_supplier_advance_clearing_lifecycle_for_invoice("
    )

    assert (
        bridge_index
        < supplier_index
    )

    assert (
        "TradeDirection.PURCHASE"
        in source
    )
