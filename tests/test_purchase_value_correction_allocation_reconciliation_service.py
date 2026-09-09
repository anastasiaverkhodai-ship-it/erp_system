from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.purchase_value_correction_allocation_reconciliation_service as service
from app.models.purchase_value_correction_allocation_event import (
    PurchaseValueCorrectionAllocationEvent,
)
from app.services.purchase_value_correction_allocation_calculation_service import (
    PurchaseValueCorrectionAllocationCandidate,
)
from app.services.purchase_value_correction_allocation_reconciliation_service import (
    PurchaseValueCorrectionAllocationReconciliationDataIntegrityError,
    reconcile_purchase_value_correction_allocations_for_event,
)


D1 = date(
    2026,
    9,
    1,
)

D2 = date(
    2026,
    9,
    2,
)

D5 = date(
    2026,
    9,
    5,
)

D10 = date(
    2026,
    9,
    10,
)


def correction(
    *,
    event_id=11,
    reversal_of_id=None,
    correction_date=D1,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        direction="purchase",
        trade_document_id=100,
        trade_document_line_id=101,
        product_id=5,
        correction_date=correction_date,
        original_gross_amount=Decimal("0.03"),
        original_tax_amount=Decimal("0.00"),
        corrected_gross_amount=Decimal("0.02"),
        corrected_tax_amount=Decimal("0.00"),
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def requested_state(
    *,
    active=True,
):
    original = correction()

    if active:
        requested = original
    else:
        requested = correction(
            event_id=12,
            reversal_of_id=11,
            correction_date=D5,
        )

    return service._RequestedCorrectionState(
        requested_event=requested,
        source_event=original,
        is_active=active,
    )


def candidate(
    source_id,
    event_date,
    quantity="1",
):
    return (
        PurchaseValueCorrectionAllocationCandidate(
            invoice_fulfillment_allocation_id=(
                source_id
            ),
            receipt_event_date=event_date,
            quantity=Decimal(
                quantity
            ),
        )
    )


def current_event(
    event_id,
    source_id,
    *,
    original,
    corrected,
    recognition_date,
    reversal_of_id=None,
):
    return (
        PurchaseValueCorrectionAllocationEvent(
            id=event_id,
            company_id=1,
            trade_value_correction_event_id=11,
            invoice_fulfillment_allocation_id=(
                source_id
            ),
            recognition_date=recognition_date,
            original_allocated_base_amount=Decimal(
                original
            ),
            corrected_allocated_base_amount=Decimal(
                corrected
            ),
            currency_code="UAH",
            created_by=7,
            reversal_of_id=reversal_of_id,
        )
    )


def install_common(
    monkeypatch,
    *,
    state,
    history=(),
    candidates=(),
    invoice_quantity="2",
):
    monkeypatch.setattr(
        service,
        "_load_requested_correction_state",
        AsyncMock(
            return_value=state
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_allocation_history",
        AsyncMock(
            return_value=history
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_invoice_line_quantity",
        AsyncMock(
            return_value=Decimal(
                invoice_quantity
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_allocation_candidates",
        AsyncMock(
            return_value=candidates
        ),
    )


@pytest.mark.asyncio
async def test_active_correction_builds_all_active_peer_targets(
    monkeypatch,
):
    install_common(
        monkeypatch,
        state=requested_state(
            active=True
        ),
        candidates=(
            candidate(
                1,
                D1,
            ),
            candidate(
                2,
                D2,
            ),
        ),
    )

    reconcile_mock = AsyncMock(
        return_value=()
    )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_value_correction_allocation_source",
        reconcile_mock,
    )

    result = (
        await reconcile_purchase_value_correction_allocations_for_event(
            object(),
            company_id=1,
            trade_value_correction_event_id=11,
            adjustment_date=D10,
            created_by=7,
        )
    )

    assert result.source_is_active is True

    assert tuple(
        target.invoice_fulfillment_allocation_id
        for target in result.desired_targets
    ) == (
        1,
        2,
    )

    assert len(
        result.reconciliation_targets
    ) == 1

    assert (
        result.desired_targets[
            1
        ].is_noop
        is True
    )

    assert (
        result.reconciliation_targets[
            0
        ].invoice_fulfillment_allocation_id
        == 1
    )


@pytest.mark.asyncio
async def test_new_future_receipt_keeps_pure_economic_date(
    monkeypatch,
):
    install_common(
        monkeypatch,
        state=requested_state(
            active=True
        ),
        candidates=(
            candidate(
                1,
                D5,
            ),
        ),
        invoice_quantity="2",
    )

    captured = []

    async def reconcile(
        db,
        *,
        company_id,
        target,
        created_by,
        reversal_date,
    ):
        captured.append(
            target
        )
        return ()

    monkeypatch.setattr(
        service,
        "reconcile_purchase_value_correction_allocation_source",
        reconcile,
    )

    await reconcile_purchase_value_correction_allocations_for_event(
        object(),
        company_id=1,
        trade_value_correction_event_id=11,
        adjustment_date=D10,
        created_by=7,
    )

    assert (
        captured[0].recognition_date
        == D5
    )


@pytest.mark.asyncio
async def test_unchanged_peer_does_not_churn_on_later_date(
    monkeypatch,
):
    current = current_event(
        1,
        1,
        original="0.02",
        corrected="0.01",
        recognition_date=D5,
    )

    install_common(
        monkeypatch,
        state=requested_state(
            active=True
        ),
        history=(
            current,
        ),
        candidates=(
            candidate(
                1,
                D5,
            ),
        ),
        invoice_quantity="2",
    )

    captured = []

    async def reconcile(
        db,
        *,
        company_id,
        target,
        created_by,
        reversal_date,
    ):
        captured.append(
            target
        )
        return ()

    monkeypatch.setattr(
        service,
        "reconcile_purchase_value_correction_allocation_source",
        reconcile,
    )

    await reconcile_purchase_value_correction_allocations_for_event(
        object(),
        company_id=1,
        trade_value_correction_event_id=11,
        adjustment_date=D10,
        created_by=7,
    )

    assert (
        captured[0].recognition_date
        == D5
    )


@pytest.mark.asyncio
async def test_changed_peer_uses_forward_adjustment_date(
    monkeypatch,
):
    current = current_event(
        1,
        2,
        original="0.01",
        corrected="0.00",
        recognition_date=D2,
    )

    install_common(
        monkeypatch,
        state=requested_state(
            active=True
        ),
        history=(
            current,
        ),
        candidates=(
            candidate(
                2,
                D2,
            ),
        ),
        invoice_quantity="2",
    )

    captured = []

    async def reconcile(
        db,
        *,
        company_id,
        target,
        created_by,
        reversal_date,
    ):
        captured.append(
            target
        )
        return ()

    monkeypatch.setattr(
        service,
        "reconcile_purchase_value_correction_allocation_source",
        reconcile,
    )

    await reconcile_purchase_value_correction_allocations_for_event(
        object(),
        company_id=1,
        trade_value_correction_event_id=11,
        adjustment_date=D10,
        created_by=7,
    )

    assert (
        captured[0]
        .original_allocated_base_amount
        == Decimal("0.02")
    )

    assert (
        captured[0].recognition_date
        == D10
    )


@pytest.mark.asyncio
async def test_disappeared_source_is_zeroed_before_active_peer(
    monkeypatch,
):
    old = current_event(
        1,
        1,
        original="0.02",
        corrected="0.01",
        recognition_date=D1,
    )

    install_common(
        monkeypatch,
        state=requested_state(
            active=True
        ),
        history=(
            old,
        ),
        candidates=(
            candidate(
                2,
                D5,
            ),
        ),
        invoice_quantity="2",
    )

    captured = []

    async def reconcile(
        db,
        *,
        company_id,
        target,
        created_by,
        reversal_date,
    ):
        captured.append(
            target
        )
        return ()

    monkeypatch.setattr(
        service,
        "reconcile_purchase_value_correction_allocation_source",
        reconcile,
    )

    await reconcile_purchase_value_correction_allocations_for_event(
        object(),
        company_id=1,
        trade_value_correction_event_id=11,
        adjustment_date=D10,
        created_by=7,
    )

    assert (
        captured[0]
        .invoice_fulfillment_allocation_id
        == 1
    )

    assert captured[0].is_noop is True

    assert (
        captured[1]
        .invoice_fulfillment_allocation_id
        == 2
    )


@pytest.mark.asyncio
async def test_reversed_correction_zeroes_all_current_sources(
    monkeypatch,
):
    first = current_event(
        1,
        1,
        original="0.02",
        corrected="0.01",
        recognition_date=D1,
    )

    second = current_event(
        2,
        2,
        original="0.01",
        corrected="0.00",
        recognition_date=D2,
    )

    install_common(
        monkeypatch,
        state=requested_state(
            active=False
        ),
        history=(
            first,
            second,
        ),
    )

    captured = []

    async def reconcile(
        db,
        *,
        company_id,
        target,
        created_by,
        reversal_date,
    ):
        captured.append(
            target
        )
        return ()

    monkeypatch.setattr(
        service,
        "reconcile_purchase_value_correction_allocation_source",
        reconcile,
    )

    result = (
        await reconcile_purchase_value_correction_allocations_for_event(
            object(),
            company_id=1,
            trade_value_correction_event_id=12,
            adjustment_date=D10,
            created_by=7,
        )
    )

    assert result.source_is_active is False
    assert result.desired_targets == ()

    assert tuple(
        target.invoice_fulfillment_allocation_id
        for target in captured
    ) == (
        1,
        2,
    )

    assert all(
        target.is_noop
        for target in captured
    )


@pytest.mark.asyncio
async def test_inactive_correction_does_not_load_active_ifa_peers(
    monkeypatch,
):
    state = requested_state(
        active=False
    )

    monkeypatch.setattr(
        service,
        "_load_requested_correction_state",
        AsyncMock(
            return_value=state
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_allocation_history",
        AsyncMock(
            return_value=()
        ),
    )

    quantity_loader = AsyncMock()
    candidate_loader = AsyncMock()

    monkeypatch.setattr(
        service,
        "_load_invoice_line_quantity",
        quantity_loader,
    )

    monkeypatch.setattr(
        service,
        "_load_active_allocation_candidates",
        candidate_loader,
    )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_value_correction_allocation_source",
        AsyncMock(
            return_value=()
        ),
    )

    await reconcile_purchase_value_correction_allocations_for_event(
        object(),
        company_id=1,
        trade_value_correction_event_id=12,
        adjustment_date=D10,
        created_by=7,
    )

    quantity_loader.assert_not_awaited()
    candidate_loader.assert_not_awaited()


def test_multiple_active_events_for_one_source_fail_closed():
    first = current_event(
        1,
        1,
        original="0.02",
        corrected="0.01",
        recognition_date=D1,
    )

    second = current_event(
        2,
        1,
        original="0.01",
        corrected="0.00",
        recognition_date=D2,
    )

    with pytest.raises(
        PurchaseValueCorrectionAllocationReconciliationDataIntegrityError,
        match="multiple active originals",
    ):
        service._active_allocation_event_map(
            events=(
                first,
                second,
            ),
            source_event_id=11,
            currency_code="UAH",
        )


@pytest.mark.asyncio
async def test_adjustment_date_cannot_precede_correction(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "_load_requested_correction_state",
        AsyncMock(
            return_value=(
                requested_state(
                    active=True
                )
            )
        ),
    )

    with pytest.raises(
        PurchaseValueCorrectionAllocationReconciliationDataIntegrityError,
        match="cannot precede",
    ):
        await reconcile_purchase_value_correction_allocations_for_event(
            object(),
            company_id=1,
            trade_value_correction_event_id=11,
            adjustment_date=date(
                2026,
                8,
                31,
            ),
            created_by=7,
        )


def test_service_contains_no_forbidden_coupling():
    from pathlib import Path
    import ast

    path = Path(
        "app/services/"
        "purchase_value_correction_"
        "allocation_reconciliation_service.py"
    )

    tree = ast.parse(
        path.read_text()
    )

    forbidden_import_prefixes = (
        "app.models.journal_entry",
        "app.models.journal_entry_line",
        "app.services.accounting_",
        "app.services.supplier_advance",
        "app.services.input_vat",
        "app.services.purchase_return_vat",
        "app.services.inventory",
        "app.services.fifo",
        "app.services.moving_average",
    )

    for node in ast.walk(
        tree
    ):
        if isinstance(
            node,
            ast.ImportFrom,
        ):
            module = (
                node.module
                or ""
            )

            assert not any(
                module.startswith(
                    prefix
                )
                for prefix
                in forbidden_import_prefixes
            )

        if not isinstance(
            node,
            ast.Call,
        ):
            continue

        if not isinstance(
            node.func,
            ast.Attribute,
        ):
            continue

        assert node.func.attr not in {
            "commit",
            "rollback",
        }
