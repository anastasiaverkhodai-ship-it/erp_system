from datetime import date
from types import SimpleNamespace

import pytest

import app.services.sales_recognition_lifecycle_service as service

from app.services.sales_recognition_calculation_service import (
    SalesRecognitionDataIntegrityError,
)
from app.services.sales_recognition_journal_service import (
    SalesRecognitionJournalError,
)
from app.services.sales_recognition_lifecycle_service import (
    SalesRecognitionLifecycleError,
    reconcile_sales_recognition_lifecycle_for_invoice_line,
)
from app.services.trade_document_types import (
    TradeDirection,
)


D1 = date(2026, 9, 1)


async def _sale_direction(
    db,
    *,
    company_id,
    invoice_id,
):
    return TradeDirection.SALE


@pytest.mark.asyncio
async def test_sale_invoice_dispatches_sales_reconciliation(
    monkeypatch,
):
    expected = SimpleNamespace(
        created_events=(),
    )

    calls = []

    async def fake_reconcile(
        db,
        *,
        company_id,
        invoice_id,
        invoice_line_id,
        adjustment_date,
        created_by,
    ):
        calls.append(
            (
                company_id,
                invoice_id,
                invoice_line_id,
                adjustment_date,
                created_by,
            )
        )
        return expected

    monkeypatch.setattr(
        service,
        "_get_invoice_direction",
        _sale_direction,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_recognition_for_invoice_line",
        fake_reconcile,
    )

    result = await (
        reconcile_sales_recognition_lifecycle_for_invoice_line(
            object(),
            company_id=1,
            invoice_id=10,
            invoice_line_id=20,
            adjustment_date=D1,
            created_by=30,
        )
    )

    assert result is expected

    assert calls == [
        (
            1,
            10,
            20,
            D1,
            30,
        )
    ]


@pytest.mark.asyncio
async def test_original_created_event_posts_original_journal(
    monkeypatch,
):
    event = SimpleNamespace(
        id=101,
        reversal_of_id=None,
    )

    expected = SimpleNamespace(
        created_events=(
            event,
        ),
    )

    calls = []

    async def fake_reconcile(
        *args,
        **kwargs,
    ):
        return expected

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

    async def should_not_reverse(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Original event must not use "
            "Sales journal reversal"
        )

    monkeypatch.setattr(
        service,
        "_get_invoice_direction",
        _sale_direction,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_recognition_for_invoice_line",
        fake_reconcile,
    )

    monkeypatch.setattr(
        service,
        "generate_and_post_sales_recognition_journal_entry",
        fake_generate,
    )

    monkeypatch.setattr(
        service,
        "reverse_sales_recognition_journal_entry",
        should_not_reverse,
    )

    result = await (
        reconcile_sales_recognition_lifecycle_for_invoice_line(
            object(),
            company_id=1,
            invoice_id=10,
            invoice_line_id=20,
            adjustment_date=D1,
            created_by=30,
        )
    )

    assert result is expected

    assert calls == [
        (
            "original",
            101,
            30,
        )
    ]


@pytest.mark.asyncio
async def test_reversal_then_replacement_preserves_created_event_order(
    monkeypatch,
):
    reversal = SimpleNamespace(
        id=201,
        reversal_of_id=100,
    )

    replacement = SimpleNamespace(
        id=202,
        reversal_of_id=None,
    )

    expected = SimpleNamespace(
        created_events=(
            reversal,
            replacement,
        ),
    )

    calls = []

    async def fake_reconcile(
        *args,
        **kwargs,
    ):
        return expected

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
        "_get_invoice_direction",
        _sale_direction,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_recognition_for_invoice_line",
        fake_reconcile,
    )

    monkeypatch.setattr(
        service,
        "generate_and_post_sales_recognition_journal_entry",
        fake_generate,
    )

    monkeypatch.setattr(
        service,
        "reverse_sales_recognition_journal_entry",
        fake_reverse,
    )

    result = await (
        reconcile_sales_recognition_lifecycle_for_invoice_line(
            object(),
            company_id=1,
            invoice_id=10,
            invoice_line_id=20,
            adjustment_date=D1,
            created_by=30,
        )
    )

    assert result is expected

    assert calls == [
        (
            "reversal",
            201,
            30,
        ),
        (
            "original",
            202,
            30,
        ),
    ]


@pytest.mark.asyncio
async def test_purchase_invoice_is_noop(
    monkeypatch,
):
    async def fake_direction(
        db,
        *,
        company_id,
        invoice_id,
    ):
        return TradeDirection.PURCHASE

    async def should_not_run(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Sales lifecycle side effect must not "
            "run for purchase invoice"
        )

    monkeypatch.setattr(
        service,
        "_get_invoice_direction",
        fake_direction,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_recognition_for_invoice_line",
        should_not_run,
    )

    monkeypatch.setattr(
        service,
        "generate_and_post_sales_recognition_journal_entry",
        should_not_run,
    )

    monkeypatch.setattr(
        service,
        "reverse_sales_recognition_journal_entry",
        should_not_run,
    )

    result = await (
        reconcile_sales_recognition_lifecycle_for_invoice_line(
            object(),
            company_id=1,
            invoice_id=10,
            invoice_line_id=20,
            adjustment_date=D1,
            created_by=30,
        )
    )

    assert result is None


@pytest.mark.asyncio
async def test_sales_data_integrity_failure_is_wrapped(
    monkeypatch,
):
    async def fail_reconcile(
        *args,
        **kwargs,
    ):
        raise SalesRecognitionDataIntegrityError(
            "broken recognition state"
        )

    monkeypatch.setattr(
        service,
        "_get_invoice_direction",
        _sale_direction,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_recognition_for_invoice_line",
        fail_reconcile,
    )

    with pytest.raises(
        SalesRecognitionLifecycleError,
        match="broken recognition state",
    ):
        await (
            reconcile_sales_recognition_lifecycle_for_invoice_line(
                object(),
                company_id=1,
                invoice_id=10,
                invoice_line_id=20,
                adjustment_date=D1,
                created_by=30,
            )
        )


@pytest.mark.asyncio
async def test_sales_journal_failure_is_wrapped(
    monkeypatch,
):
    event = SimpleNamespace(
        id=101,
        reversal_of_id=None,
    )

    expected = SimpleNamespace(
        created_events=(
            event,
        ),
    )

    async def fake_reconcile(
        *args,
        **kwargs,
    ):
        return expected

    async def fail_generate(
        *args,
        **kwargs,
    ):
        raise SalesRecognitionJournalError(
            "posting exploded"
        )

    monkeypatch.setattr(
        service,
        "_get_invoice_direction",
        _sale_direction,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_recognition_for_invoice_line",
        fake_reconcile,
    )

    monkeypatch.setattr(
        service,
        "generate_and_post_sales_recognition_journal_entry",
        fail_generate,
    )

    with pytest.raises(
        SalesRecognitionLifecycleError,
        match="posting exploded",
    ):
        await (
            reconcile_sales_recognition_lifecycle_for_invoice_line(
                object(),
                company_id=1,
                invoice_id=10,
                invoice_line_id=20,
                adjustment_date=D1,
                created_by=30,
            )
        )


@pytest.mark.asyncio
async def test_lifecycle_validates_context_before_dispatch(
    monkeypatch,
):
    async def should_not_run(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "DB direction lookup must not run"
        )

    monkeypatch.setattr(
        service,
        "_get_invoice_direction",
        should_not_run,
    )

    with pytest.raises(
        ValueError,
        match="invoice_line_id",
    ):
        await (
            reconcile_sales_recognition_lifecycle_for_invoice_line(
                object(),
                company_id=1,
                invoice_id=10,
                invoice_line_id=0,
                adjustment_date=D1,
                created_by=30,
            )
        )
