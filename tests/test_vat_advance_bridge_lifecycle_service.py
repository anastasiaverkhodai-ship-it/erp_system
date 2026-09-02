from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.vat_advance_bridge_lifecycle_service as service

from app.services.vat_advance_bridge_calculation_service import (
    VatAdvanceBridgeDataIntegrityError,
)
from app.services.vat_advance_bridge_journal_service import (
    VatAdvanceBridgeJournalError,
)


D1 = date(2026, 9, 2)


@pytest.mark.asyncio
async def test_created_events_are_posted_in_exact_event_order(
    monkeypatch,
):
    calls = []

    original = SimpleNamespace(
        id=11,
        reversal_of_id=None,
    )
    reversal = SimpleNamespace(
        id=12,
        reversal_of_id=11,
    )

    result = SimpleNamespace(
        created_events=(
            original,
            reversal,
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
        "generate_and_post_vat_advance_bridge_journal_entry",
        post_original,
    )
    monkeypatch.setattr(
        service,
        "reverse_vat_advance_bridge_journal_entry",
        post_reversal,
    )

    await service._post_created_vat_advance_bridge_events(
        object(),
        result=result,
        created_by=7,
    )

    assert calls == [
        (
            "original",
            11,
            7,
        ),
        (
            "reversal",
            12,
            7,
        ),
    ]


@pytest.mark.asyncio
async def test_lifecycle_reconciles_then_posts(
    monkeypatch,
):
    db = object()

    result = SimpleNamespace(
        created_events=(),
    )

    reconcile = AsyncMock(
        return_value=result
    )
    post = AsyncMock()

    monkeypatch.setattr(
        service,
        "reconcile_vat_advance_bridge_for_tax_calculation",
        reconcile,
    )
    monkeypatch.setattr(
        service,
        "_post_created_vat_advance_bridge_events",
        post,
    )

    actual = (
        await service
        .reconcile_vat_advance_bridge_lifecycle_for_tax_calculation(
            db,
            company_id=1,
            tax_calculation_id=22,
            adjustment_date=D1,
            created_by=3,
        )
    )

    assert actual is result

    reconcile.assert_awaited_once_with(
        db,
        company_id=1,
        tax_calculation_id=22,
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
        "reconcile_vat_advance_bridge_for_tax_calculation",
        AsyncMock(
            side_effect=(
                VatAdvanceBridgeDataIntegrityError(
                    "bad bridge state"
                )
            )
        ),
    )

    with pytest.raises(
        service.VatAdvanceBridgeLifecycleError,
        match="reconciliation failed",
    ):
        await (
            service
            .reconcile_vat_advance_bridge_lifecycle_for_tax_calculation(
                object(),
                company_id=1,
                tax_calculation_id=22,
                adjustment_date=D1,
                created_by=3,
            )
        )


@pytest.mark.asyncio
async def test_journal_error_is_wrapped(
    monkeypatch,
):
    result = SimpleNamespace(
        created_events=(),
    )

    monkeypatch.setattr(
        service,
        "reconcile_vat_advance_bridge_for_tax_calculation",
        AsyncMock(
            return_value=result
        ),
    )

    monkeypatch.setattr(
        service,
        "_post_created_vat_advance_bridge_events",
        AsyncMock(
            side_effect=(
                VatAdvanceBridgeJournalError(
                    "posting failed"
                )
            )
        ),
    )

    with pytest.raises(
        service.VatAdvanceBridgeLifecycleError,
        match="journal posting failed",
    ):
        await (
            service
            .reconcile_vat_advance_bridge_lifecycle_for_tax_calculation(
                object(),
                company_id=1,
                tax_calculation_id=22,
                adjustment_date=D1,
                created_by=3,
            )
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "company_id": 0,
            "tax_calculation_id": 1,
            "adjustment_date": D1,
            "created_by": 1,
        },
        {
            "company_id": 1,
            "tax_calculation_id": 0,
            "adjustment_date": D1,
            "created_by": 1,
        },
        {
            "company_id": 1,
            "tax_calculation_id": 1,
            "adjustment_date": "2026-09-02",
            "created_by": 1,
        },
        {
            "company_id": 1,
            "tax_calculation_id": 1,
            "adjustment_date": D1,
            "created_by": 0,
        },
    ],
)
def test_context_validation_is_fail_closed(
    kwargs,
):
    with pytest.raises(
        ValueError
    ):
        service._validate_context(
            **kwargs
        )
