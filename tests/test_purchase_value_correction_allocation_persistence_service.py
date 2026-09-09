from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    MagicMock,
)

import pytest

import app.services.purchase_value_correction_allocation_persistence_service as service
from app.models.purchase_value_correction_allocation_event import (
    PurchaseValueCorrectionAllocationEvent,
)
from app.services.purchase_value_correction_allocation_calculation_service import (
    PurchaseValueCorrectionAllocationTarget,
)
from app.services.purchase_value_correction_allocation_persistence_service import (
    PurchaseValueCorrectionAllocationPersistenceDataIntegrityError,
    build_purchase_value_correction_allocation_source_plan,
    reconcile_purchase_value_correction_allocation_source,
)


D1 = date(
    2026,
    9,
    1,
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


def target(
    *,
    original="10.00",
    corrected="8.00",
    recognition_date=D5,
):
    return (
        PurchaseValueCorrectionAllocationTarget(
            trade_value_correction_event_id=11,
            invoice_fulfillment_allocation_id=22,
            recognition_date=recognition_date,
            original_allocated_base_amount=Decimal(
                original
            ),
            corrected_allocated_base_amount=Decimal(
                corrected
            ),
            currency_code="UAH",
        )
    )


def event(
    event_id,
    *,
    original="10.00",
    corrected="8.00",
    recognition_date=D5,
    reversal_of_id=None,
):
    return (
        PurchaseValueCorrectionAllocationEvent(
            id=event_id,
            company_id=1,
            trade_value_correction_event_id=11,
            invoice_fulfillment_allocation_id=22,
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


def correction():
    return SimpleNamespace(
        id=11,
        company_id=1,
        direction="purchase",
        trade_document_id=100,
        trade_document_line_id=101,
        product_id=5,
        correction_date=D1,
        currency_code="UAH",
    )


def source():
    return SimpleNamespace(
        id=22,
        company_id=1,
        invoice_id=100,
        invoice_line_id=101,
        product_id=5,
    )


def test_new_non_noop_target_creates_original_plan():
    plan = (
        build_purchase_value_correction_allocation_source_plan(
            events=(),
            target=target(),
        )
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target == target()
    assert plan.is_noop is False


def test_exact_active_target_is_noop():
    plan = (
        build_purchase_value_correction_allocation_source_plan(
            events=(
                event(
                    1
                ),
            ),
            target=target(),
        )
    )

    assert plan.is_noop is True


def test_changed_amount_reverses_and_replaces():
    changed = target(
        corrected="7.00"
    )

    plan = (
        build_purchase_value_correction_allocation_source_plan(
            events=(
                event(
                    1
                ),
            ),
            target=changed,
        )
    )

    assert plan.reversal_event_ids == (
        1,
    )

    assert (
        plan.replacement_target
        == changed
    )


def test_date_change_reverses_and_replaces():
    changed = target(
        recognition_date=D10
    )

    plan = (
        build_purchase_value_correction_allocation_source_plan(
            events=(
                event(
                    1
                ),
            ),
            target=changed,
        )
    )

    assert plan.reversal_event_ids == (
        1,
    )

    assert (
        plan.replacement_target
        == changed
    )


def test_noop_target_without_history_is_noop():
    zero = target(
        original="8.00",
        corrected="8.00",
    )

    plan = (
        build_purchase_value_correction_allocation_source_plan(
            events=(),
            target=zero,
        )
    )

    assert plan.is_noop is True


def test_noop_target_reverses_active_original_only():
    zero = target(
        original="8.00",
        corrected="8.00",
    )

    plan = (
        build_purchase_value_correction_allocation_source_plan(
            events=(
                event(
                    1
                ),
            ),
            target=zero,
        )
    )

    assert plan.reversal_event_ids == (
        1,
    )

    assert (
        plan.replacement_target
        is None
    )


def test_reversed_original_is_not_active():
    original = event(
        1
    )

    reversal = event(
        2,
        reversal_of_id=1,
        recognition_date=D10,
    )

    plan = (
        build_purchase_value_correction_allocation_source_plan(
            events=(
                original,
                reversal,
            ),
            target=target(),
        )
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target == target()


def test_multiple_active_originals_fail_closed():
    with pytest.raises(
        PurchaseValueCorrectionAllocationPersistenceDataIntegrityError,
    ):
        build_purchase_value_correction_allocation_source_plan(
            events=(
                event(
                    1
                ),
                event(
                    2,
                    corrected="7.00",
                ),
            ),
            target=target(),
        )


def test_reversal_of_reversal_fails_closed():
    original = event(
        1
    )

    first_reversal = event(
        2,
        reversal_of_id=1,
        recognition_date=D10,
    )

    second_reversal = event(
        3,
        reversal_of_id=2,
        recognition_date=D10,
    )

    with pytest.raises(
        PurchaseValueCorrectionAllocationPersistenceDataIntegrityError,
    ):
        build_purchase_value_correction_allocation_source_plan(
            events=(
                original,
                first_reversal,
                second_reversal,
            ),
            target=target(),
        )


@pytest.mark.asyncio
async def test_executor_new_target_creates_original(
    monkeypatch,
):
    db = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "_lock_trade_value_correction_event",
        AsyncMock(
            return_value=correction()
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_invoice_fulfillment_allocation",
        AsyncMock(
            return_value=source()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_source_history",
        AsyncMock(
            return_value=()
        ),
    )

    created = (
        await reconcile_purchase_value_correction_allocation_source(
            db,
            company_id=1,
            target=target(),
            created_by=7,
        )
    )

    assert len(
        created
    ) == 1

    assert (
        created[0].reversal_of_id
        is None
    )

    assert (
        created[0]
        .original_allocated_base_amount
        == Decimal("10.00")
    )

    assert (
        created[0]
        .corrected_allocated_base_amount
        == Decimal("8.00")
    )

    db.add.assert_called_once_with(
        created[0]
    )

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_executor_changed_target_creates_reversal_then_replacement(
    monkeypatch,
):
    db = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
    )

    original = event(
        1
    )

    monkeypatch.setattr(
        service,
        "_lock_trade_value_correction_event",
        AsyncMock(
            return_value=correction()
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_invoice_fulfillment_allocation",
        AsyncMock(
            return_value=source()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_source_history",
        AsyncMock(
            return_value=(
                original,
            )
        ),
    )

    changed = target(
        corrected="7.00"
    )

    created = (
        await reconcile_purchase_value_correction_allocation_source(
            db,
            company_id=1,
            target=changed,
            created_by=7,
            reversal_date=D10,
        )
    )

    assert len(
        created
    ) == 2

    reversal = created[
        0
    ]

    replacement = created[
        1
    ]

    assert (
        reversal.reversal_of_id
        == 1
    )

    assert (
        reversal.recognition_date
        == D10
    )

    assert (
        reversal.original_allocated_base_amount
        == original.original_allocated_base_amount
    )

    assert (
        reversal.corrected_allocated_base_amount
        == original.corrected_allocated_base_amount
    )

    assert (
        replacement.reversal_of_id
        is None
    )

    assert (
        replacement.corrected_allocated_base_amount
        == Decimal("7.00")
    )

    assert (
        db.add.call_args_list[
            0
        ].args[
            0
        ]
        is reversal
    )

    assert (
        db.add.call_args_list[
            1
        ].args[
            0
        ]
        is replacement
    )

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_executor_noop_target_reverses_only(
    monkeypatch,
):
    db = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "_lock_trade_value_correction_event",
        AsyncMock(
            return_value=correction()
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_invoice_fulfillment_allocation",
        AsyncMock(
            return_value=source()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_source_history",
        AsyncMock(
            return_value=(
                event(
                    1
                ),
            )
        ),
    )

    zero = target(
        original="8.00",
        corrected="8.00",
    )

    created = (
        await reconcile_purchase_value_correction_allocation_source(
            db,
            company_id=1,
            target=zero,
            created_by=7,
            reversal_date=D10,
        )
    )

    assert len(
        created
    ) == 1

    assert (
        created[0].reversal_of_id
        == 1
    )

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_executor_exact_target_is_idempotent(
    monkeypatch,
):
    db = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "_lock_trade_value_correction_event",
        AsyncMock(
            return_value=correction()
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_invoice_fulfillment_allocation",
        AsyncMock(
            return_value=source()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_source_history",
        AsyncMock(
            return_value=(
                event(
                    1
                ),
            )
        ),
    )

    created = (
        await reconcile_purchase_value_correction_allocation_source(
            db,
            company_id=1,
            target=target(),
            created_by=7,
        )
    )

    assert created == ()

    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_reversal_date_cannot_precede_original(
    monkeypatch,
):
    db = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "_lock_trade_value_correction_event",
        AsyncMock(
            return_value=correction()
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_invoice_fulfillment_allocation",
        AsyncMock(
            return_value=source()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_source_history",
        AsyncMock(
            return_value=(
                event(
                    1,
                    recognition_date=D5,
                ),
            )
        ),
    )

    with pytest.raises(
        PurchaseValueCorrectionAllocationPersistenceDataIntegrityError,
        match="cannot precede",
    ):
        await (
            reconcile_purchase_value_correction_allocation_source(
                db,
                company_id=1,
                target=target(
                    corrected="7.00"
                ),
                created_by=7,
                reversal_date=D1,
            )
        )


@pytest.mark.asyncio
async def test_wrong_invoice_line_provenance_fails_closed(
    monkeypatch,
):
    db = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
    )

    bad_source = source()
    bad_source.invoice_line_id = 999

    monkeypatch.setattr(
        service,
        "_lock_trade_value_correction_event",
        AsyncMock(
            return_value=correction()
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_invoice_fulfillment_allocation",
        AsyncMock(
            return_value=bad_source
        ),
    )

    with pytest.raises(
        PurchaseValueCorrectionAllocationPersistenceDataIntegrityError,
        match="provenance",
    ):
        await (
            reconcile_purchase_value_correction_allocation_source(
                db,
                company_id=1,
                target=target(),
                created_by=7,
            )
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_sale_correction_fails_closed(
    monkeypatch,
):
    db = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
    )

    bad_correction = correction()
    bad_correction.direction = "sale"

    monkeypatch.setattr(
        service,
        "_lock_trade_value_correction_event",
        AsyncMock(
            return_value=bad_correction
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_invoice_fulfillment_allocation",
        AsyncMock(
            return_value=source()
        ),
    )

    with pytest.raises(
        PurchaseValueCorrectionAllocationPersistenceDataIntegrityError,
        match="purchase",
    ):
        await (
            reconcile_purchase_value_correction_allocation_source(
                db,
                company_id=1,
                target=target(),
                created_by=7,
            )
        )


def test_service_contains_no_commit_or_rollback():
    from pathlib import Path
    import ast

    path = Path(
        "app/services/"
        "purchase_value_correction_"
        "allocation_persistence_service.py"
    )

    tree = ast.parse(
        path.read_text()
    )

    bad = []

    for node in ast.walk(
        tree
    ):
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

        if node.func.attr in {
            "commit",
            "rollback",
        }:
            bad.append(
                (
                    node.func.attr,
                    node.lineno,
                )
            )

    assert bad == []
