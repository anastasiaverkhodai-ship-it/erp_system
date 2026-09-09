from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.purchase_value_correction_vat_accounting_lifecycle_service as service

from app.services.purchase_value_correction_vat_adjustment_persistence_service import (
    PurchaseValueCorrectionVatAdjustmentReconciliationResult,
)
from app.services.purchase_value_correction_vat_adjustment_journal_service import (
    PurchaseValueCorrectionVatAdjustmentJournalError,
)
from app.services.supplier_advance_clearing_lifecycle_service import (
    SupplierAdvanceClearingLifecycleError,
)


D5 = date(
    2026,
    9,
    5,
)


def event(
    *,
    event_id=1,
    company_id=1,
    correction_id=100,
    adjustment_date=D5,
):
    return SimpleNamespace(
        id=event_id,
        company_id=company_id,
        trade_value_correction_event_id=correction_id,
        adjustment_date=adjustment_date,
        reversal_of_id=None,
    )


@pytest.mark.asyncio
async def test_noop_posts_nothing_and_does_not_clear(
    monkeypatch,
):
    post = AsyncMock(
        return_value=()
    )
    clear = AsyncMock()

    monkeypatch.setattr(
        service,
        "post_created_purchase_value_correction_vat_adjustment_journals",
        post,
    )

    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_lifecycle_for_invoice",
        clear,
    )

    result = (
        PurchaseValueCorrectionVatAdjustmentReconciliationResult(
            created_events=()
        )
    )

    journals, clearing = await (
        service
        .post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing(
            object(),
            reconciliation_result=result,
            created_by=1,
        )
    )

    assert journals == ()
    assert clearing == ()

    post.assert_awaited_once()
    clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_economic_journal_precedes_supplier_clearing(
    monkeypatch,
):
    order = []

    async def post(*args, **kwargs):
        order.append(
            "economic_vat_journal"
        )
        return (
            SimpleNamespace(
                id=501
            ),
        )

    async def resolve_invoice(*args, **kwargs):
        order.append(
            "resolve_invoice"
        )
        return 77

    async def clear(*args, **kwargs):
        order.append(
            "supplier_clearing"
        )

        assert kwargs["company_id"] == 1
        assert kwargs["invoice_id"] == 77
        assert kwargs["adjustment_date"] == D5
        assert kwargs["created_by"] == 9

        return SimpleNamespace(
            invoice_id=77
        )

    monkeypatch.setattr(
        service,
        "post_created_purchase_value_correction_vat_adjustment_journals",
        post,
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_value_correction_invoice_id_for_vat_event",
        resolve_invoice,
    )

    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_lifecycle_for_invoice",
        clear,
    )

    result = (
        PurchaseValueCorrectionVatAdjustmentReconciliationResult(
            created_events=(
                event(),
            )
        )
    )

    journals, clearing = await (
        service
        .post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing(
            object(),
            reconciliation_result=result,
            created_by=9,
        )
    )

    assert len(journals) == 1
    assert len(clearing) == 1

    assert order == [
        "economic_vat_journal",
        "resolve_invoice",
        "supplier_clearing",
    ]


@pytest.mark.asyncio
async def test_reversal_and_replacement_batch_clears_once(
    monkeypatch,
):
    post = AsyncMock(
        return_value=(
            SimpleNamespace(id=1),
            SimpleNamespace(id=2),
        )
    )

    resolve = AsyncMock(
        return_value=77
    )

    clear = AsyncMock(
        return_value=SimpleNamespace(
            invoice_id=77
        )
    )

    monkeypatch.setattr(
        service,
        "post_created_purchase_value_correction_vat_adjustment_journals",
        post,
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_value_correction_invoice_id_for_vat_event",
        resolve,
    )

    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_lifecycle_for_invoice",
        clear,
    )

    reversal = event(
        event_id=10,
        adjustment_date=D5,
    )
    reversal.reversal_of_id = 5

    replacement = event(
        event_id=11,
        adjustment_date=D5,
    )

    result = (
        PurchaseValueCorrectionVatAdjustmentReconciliationResult(
            created_events=(
                reversal,
                replacement,
            )
        )
    )

    await (
        service
        .post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing(
            object(),
            reconciliation_result=result,
            created_by=1,
        )
    )

    assert resolve.await_count == 2
    clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_cannot_span_companies(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "post_created_purchase_value_correction_vat_adjustment_journals",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_value_correction_invoice_id_for_vat_event",
        AsyncMock(
            return_value=77
        ),
    )

    result = (
        PurchaseValueCorrectionVatAdjustmentReconciliationResult(
            created_events=(
                event(
                    event_id=1,
                    company_id=1,
                ),
                event(
                    event_id=2,
                    company_id=2,
                ),
            )
        )
    )

    with pytest.raises(
        service
        .PurchaseValueCorrectionVatAccountingLifecycleError
    ):
        await (
            service
            .post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing(
                object(),
                reconciliation_result=result,
                created_by=1,
            )
        )


@pytest.mark.asyncio
async def test_batch_cannot_span_invoices(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "post_created_purchase_value_correction_vat_adjustment_journals",
        AsyncMock(
            return_value=()
        ),
    )

    resolve = AsyncMock(
        side_effect=(
            77,
            88,
        )
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_value_correction_invoice_id_for_vat_event",
        resolve,
    )

    result = (
        PurchaseValueCorrectionVatAdjustmentReconciliationResult(
            created_events=(
                event(
                    event_id=1
                ),
                event(
                    event_id=2,
                    correction_id=101,
                ),
            )
        )
    )

    with pytest.raises(
        service
        .PurchaseValueCorrectionVatAccountingLifecycleError
    ):
        await (
            service
            .post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing(
                object(),
                reconciliation_result=result,
                created_by=1,
            )
        )


@pytest.mark.asyncio
async def test_typed_vat_journal_error_is_wrapped(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "post_created_purchase_value_correction_vat_adjustment_journals",
        AsyncMock(
            side_effect=(
                PurchaseValueCorrectionVatAdjustmentJournalError(
                    "journal failure"
                )
            )
        ),
    )

    result = (
        PurchaseValueCorrectionVatAdjustmentReconciliationResult(
            created_events=(
                event(),
            )
        )
    )

    with pytest.raises(
        service
        .PurchaseValueCorrectionVatAccountingLifecycleError
    ):
        await (
            service
            .post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing(
                object(),
                reconciliation_result=result,
                created_by=1,
            )
        )


@pytest.mark.asyncio
async def test_supplier_clearing_error_is_wrapped(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "post_created_purchase_value_correction_vat_adjustment_journals",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_value_correction_invoice_id_for_vat_event",
        AsyncMock(
            return_value=77
        ),
    )

    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_lifecycle_for_invoice",
        AsyncMock(
            side_effect=(
                SupplierAdvanceClearingLifecycleError(
                    "clearing failure"
                )
            )
        ),
    )

    result = (
        PurchaseValueCorrectionVatAdjustmentReconciliationResult(
            created_events=(
                event(),
            )
        )
    )

    with pytest.raises(
        service
        .PurchaseValueCorrectionVatAccountingLifecycleError
    ):
        await (
            service
            .post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing(
                object(),
                reconciliation_result=result,
                created_by=1,
            )
        )


@pytest.mark.asyncio
async def test_unexpected_programming_error_is_not_swallowed(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "post_created_purchase_value_correction_vat_adjustment_journals",
        AsyncMock(
            side_effect=RuntimeError(
                "programming bug"
            )
        ),
    )

    result = (
        PurchaseValueCorrectionVatAdjustmentReconciliationResult(
            created_events=(
                event(),
            )
        )
    )

    with pytest.raises(
        RuntimeError,
        match="programming bug",
    ):
        await (
            service
            .post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing(
                object(),
                reconciliation_result=result,
                created_by=1,
            )
        )
