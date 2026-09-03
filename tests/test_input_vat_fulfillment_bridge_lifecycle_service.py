from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.input_vat_fulfillment_bridge_lifecycle_service as service
from app.services.input_vat_fulfillment_bridge_journal_service import (
    InputVatFulfillmentBridgeJournalError,
)
from app.services.input_vat_fulfillment_bridge_lifecycle_service import (
    InputVatFulfillmentBridgeLifecycleError,
    _post_created_input_vat_fulfillment_bridge_events,
    reconcile_input_vat_fulfillment_bridge_lifecycle_for_tax_calculation,
)
from app.services.input_vat_fulfillment_bridge_persistence_service import (
    InputVatFulfillmentBridgePersistenceError,
)
from app.services.input_vat_fulfillment_bridge_reconciliation_service import (
    InputVatFulfillmentBridgeReconciliationResult,
)


D1 = date(
    2026,
    8,
    30,
)

D2 = date(
    2026,
    8,
    31,
)


def event(
    *,
    event_id,
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        tax_calculation_id=20,
        invoice_fulfillment_allocation_id=30,
        bridge_date=(
            D1
            if reversal_of_id is None
            else D2
        ),
        bridged_tax_amount=Decimal(
            "20.00"
        ),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=(
            reversal_of_id
        ),
    )


def result(
    *events,
):
    return (
        InputVatFulfillmentBridgeReconciliationResult(
            tax_calculation_id=20,
            desired_targets=(),
            reconciliation_targets=(),
            created_events=tuple(
                events
            ),
        )
    )


def test_created_event_ids_are_derived_from_events():
    first = event(
        event_id=101
    )

    second = event(
        event_id=102,
        reversal_of_id=101,
    )

    reconciliation = result(
        first,
        second,
    )

    assert (
        reconciliation.created_events
        == (
            first,
            second,
        )
    )

    assert (
        reconciliation.created_event_ids
        == (
            101,
            102,
        )
    )


@pytest.mark.asyncio
async def test_created_events_are_posted_in_exact_persistence_order(
    monkeypatch,
):
    original = event(
        event_id=101
    )

    reversal = event(
        event_id=102,
        reversal_of_id=101,
    )

    replacement = event(
        event_id=103
    )

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

        return SimpleNamespace(
            id=event.id + 1000
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

        return SimpleNamespace(
            id=(
                reversal_event.id
                + 1000
            )
        )

    monkeypatch.setattr(
        service,
        (
            "generate_and_post_input_vat_"
            "fulfillment_bridge_journal_entry"
        ),
        post_original,
    )

    monkeypatch.setattr(
        service,
        (
            "reverse_input_vat_"
            "fulfillment_bridge_journal_entry"
        ),
        post_reversal,
    )

    await (
        _post_created_input_vat_fulfillment_bridge_events(
            object(),
            result=result(
                reversal,
                replacement,
                original,
            ),
            created_by=7,
        )
    )

    assert calls == [
        (
            "reversal",
            102,
            7,
        ),
        (
            "original",
            103,
            7,
        ),
        (
            "original",
            101,
            7,
        ),
    ]


@pytest.mark.asyncio
async def test_lifecycle_reconciles_then_posts(
    monkeypatch,
):
    original = event(
        event_id=101
    )

    reversal = event(
        event_id=102,
        reversal_of_id=101,
    )

    reconciliation = result(
        original,
        reversal,
    )

    calls = []

    async def reconcile(
        db,
        *,
        company_id,
        tax_calculation_id,
        adjustment_date,
        created_by,
    ):
        calls.append(
            (
                "reconcile",
                company_id,
                tax_calculation_id,
                adjustment_date,
                created_by,
            )
        )

        return reconciliation

    async def post(
        db,
        *,
        result,
        created_by,
    ):
        calls.append(
            (
                "post",
                result.created_event_ids,
                created_by,
            )
        )

    monkeypatch.setattr(
        service,
        (
            "reconcile_input_vat_fulfillment_"
            "bridge_for_tax_calculation"
        ),
        reconcile,
    )

    monkeypatch.setattr(
        service,
        (
            "_post_created_input_vat_"
            "fulfillment_bridge_events"
        ),
        post,
    )

    returned = (
        await reconcile_input_vat_fulfillment_bridge_lifecycle_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D2,
            created_by=7,
        )
    )

    assert returned is reconciliation

    assert calls == [
        (
            "reconcile",
            1,
            20,
            D2,
            7,
        ),
        (
            "post",
            (
                101,
                102,
            ),
            7,
        ),
    ]


@pytest.mark.asyncio
async def test_reconciliation_failure_is_wrapped(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        (
            "reconcile_input_vat_fulfillment_"
            "bridge_for_tax_calculation"
        ),
        AsyncMock(
            side_effect=(
                InputVatFulfillmentBridgePersistenceError(
                    "persistence failure"
                )
            )
        ),
    )

    with pytest.raises(
        InputVatFulfillmentBridgeLifecycleError,
        match="reconciliation failed",
    ):
        await (
            reconcile_input_vat_fulfillment_bridge_lifecycle_for_tax_calculation(
                object(),
                company_id=1,
                tax_calculation_id=20,
                adjustment_date=D2,
                created_by=1,
            )
        )


@pytest.mark.asyncio
async def test_journal_failure_is_wrapped(
    monkeypatch,
):
    reconciliation = result(
        event(
            event_id=101
        )
    )

    monkeypatch.setattr(
        service,
        (
            "reconcile_input_vat_fulfillment_"
            "bridge_for_tax_calculation"
        ),
        AsyncMock(
            return_value=(
                reconciliation
            )
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "_post_created_input_vat_"
            "fulfillment_bridge_events"
        ),
        AsyncMock(
            side_effect=(
                InputVatFulfillmentBridgeJournalError(
                    "journal failure"
                )
            )
        ),
    )

    with pytest.raises(
        InputVatFulfillmentBridgeLifecycleError,
        match="journal posting failed",
    ):
        await (
            reconcile_input_vat_fulfillment_bridge_lifecycle_for_tax_calculation(
                object(),
                company_id=1,
                tax_calculation_id=20,
                adjustment_date=D2,
                created_by=1,
            )
        )


@pytest.mark.asyncio
async def test_empty_reconciliation_has_no_gl_effect(
    monkeypatch,
):
    post_original = AsyncMock()
    post_reversal = AsyncMock()

    monkeypatch.setattr(
        service,
        (
            "generate_and_post_input_vat_"
            "fulfillment_bridge_journal_entry"
        ),
        post_original,
    )

    monkeypatch.setattr(
        service,
        (
            "reverse_input_vat_"
            "fulfillment_bridge_journal_entry"
        ),
        post_reversal,
    )

    await (
        _post_created_input_vat_fulfillment_bridge_events(
            object(),
            result=result(),
            created_by=1,
        )
    )

    post_original.assert_not_awaited()
    post_reversal.assert_not_awaited()
