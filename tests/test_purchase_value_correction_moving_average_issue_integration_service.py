from datetime import date
from types import SimpleNamespace

import pytest

from app.services.purchase_value_correction_moving_average_issue_integration_service import (
    PurchaseValueCorrectionMovingAverageIssueCrossReceiptError,
    PurchaseValueCorrectionMovingAverageIssueIntegrationIntegrityError,
    _active_on_hand_allocation_ids,
    _validate_single_physical_receipt_group,
)


def _event(
    event_id,
    allocation_id,
    *,
    effect_kind="on_hand",
    reversal_of_id=None,
    movement_id=None,
    cost_id=None,
    original_amount=1,
    corrected_amount=2,
    recognition_date=date(2026, 1, 5),
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        purchase_value_correction_allocation_event_id=(
            allocation_id
        ),
        product_id=10,
        warehouse_id=20,
        effect_kind=effect_kind,
        source_moving_average_movement_id=movement_id,
        source_inventory_cost_entry_id=cost_id,
        recognition_date=recognition_date,
        quantity=1,
        original_valuation_amount=original_amount,
        corrected_valuation_amount=corrected_amount,
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def _peer_source(
    peer_ids,
    *,
    receipt_movement_id=500,
):
    return SimpleNamespace(
        allocation_event_ids=tuple(
            peer_ids
        ),
        base_source=SimpleNamespace(
            source_receipt_moving_average_movement_id=(
                receipt_movement_id
            ),
        ),
    )


def test_active_on_hand_ids_are_returned():
    history = (
        _event(
            1,
            100,
        ),
        _event(
            2,
            101,
        ),
    )

    assert (
        _active_on_hand_allocation_ids(
            history
        )
        == (100, 101)
    )


def test_issued_event_is_not_current_on_hand_overlay():
    history = (
        _event(
            1,
            100,
            effect_kind="issued",
            movement_id=700,
            cost_id=800,
        ),
    )

    assert (
        _active_on_hand_allocation_ids(
            history
        )
        == ()
    )


def test_reversed_on_hand_original_is_not_active():
    history = (
        _event(
            1,
            100,
        ),
        _event(
            2,
            100,
            reversal_of_id=1,
            original_amount=2,
            corrected_amount=1,
            recognition_date=date(2026, 1, 6),
        ),
    )

    assert (
        _active_on_hand_allocation_ids(
            history
        )
        == ()
    )


def test_on_hand_event_must_not_have_issue_provenance():
    history = (
        _event(
            1,
            100,
            movement_id=700,
        ),
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageIssueIntegrationIntegrityError
    ):
        _active_on_hand_allocation_ids(
            history
        )


def test_same_physical_receipt_peer_group_is_supported():
    result = (
        _validate_single_physical_receipt_group(
            active_allocation_event_ids=(
                100,
                101,
            ),
            peer_source=_peer_source(
                (
                    100,
                    101,
                    102,
                )
            ),
        )
    )

    assert result == 500


def test_cross_receipt_active_overlay_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageIssueCrossReceiptError
    ):
        _validate_single_physical_receipt_group(
            active_allocation_event_ids=(
                100,
                200,
            ),
            peer_source=_peer_source(
                (
                    100,
                    101,
                )
            ),
        )


def test_empty_group_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageIssueIntegrationIntegrityError
    ):
        _validate_single_physical_receipt_group(
            active_allocation_event_ids=(),
            peer_source=_peer_source(
                (100,)
            ),
        )


def test_production_inventory_costing_keeps_base_issue_then_flushes():
    """
    inventory_costing owns immutable base moving-average ISSUE
    costing only.

    Production responsibility is:

        inventory_costing:
            base MA ISSUE
            base immutable InventoryCostEntry
            flush

        WarehousePostingHandler:
            PVC MA topology reconciliation

        AccountingPostingHandler:
            normal ISSUE JournalEntry

        PVC MA post-accounting handler:
            typed PVC MA JournalEntries
    """
    from pathlib import Path

    inventory_text = Path(
        "app/services/inventory_costing.py"
    ).read_text()

    warehouse_text = Path(
        "app/services/warehouse_posting_handler.py"
    ).read_text()

    integration_text = Path(
        "app/services/"
        "purchase_value_correction_moving_average_"
        "issue_integration_service.py"
    ).read_text()

    post_text = Path(
        "app/services/"
        "purchase_value_correction_moving_average_"
        "post_accounting_handler.py"
    ).read_text()

    registry_text = Path(
        "app/services/posting_registry.py"
    ).read_text()

    movement_call = (
        "raw_cost = await process_moving_average_issue("
    )

    ice_add = "db.add(cost_entry)"
    flush = "await db.flush()"

    reconcile = (
        "await reconcile_purchase_value_correction_"
        "moving_average_after_issue("
    )

    lifecycle = (
        "await post_created_purchase_value_correction_"
        "moving_average_peer_journals("
    )

    assert movement_call in inventory_text
    assert ice_add in inventory_text
    assert flush in inventory_text

    assert (
        inventory_text.index(
            movement_call
        )
        < inventory_text.index(
            ice_add
        )
        < inventory_text.index(
            flush
        )
    )

    # inventory_costing must not own PVC topology any more.
    assert reconcile not in inventory_text

    # Warehouse phase performs reconciliation after base costing.
    assert reconcile in warehouse_text

    assert (
        warehouse_text.index(
            "await process_inventory_issue("
        )
        < warehouse_text.index(
            reconcile
        )
    )

    # Issue integration itself is reconciliation-only.
    assert lifecycle not in integration_text

    # No PVC GL inside warehouse phase.
    assert lifecycle not in warehouse_text

    # Typed PVC GL runs only in post-accounting phase.
    assert lifecycle in post_text

    # Normal document JournalEntry must already exist.
    assert (
        "context.get_journal_entry()"
        in post_text
    )

    assert (
        registry_text.index(
            "WarehousePostingHandler()"
        )
        < registry_text.index(
            "AccountingPostingHandler()"
        )
        < registry_text.index(
            "PurchaseValueCorrectionMovingAverage"
            "PostAccountingHandler()"
        )
    )

    assert (
        "AccountingAccountRole.GOODS_COGS"
        not in post_text
    )

    assert "902" not in post_text

    for text in (
        inventory_text,
        integration_text,
        warehouse_text,
        post_text,
    ):
        assert ".commit(" not in text
        assert ".rollback(" not in text




def test_production_inventory_costing_does_not_replace_base_raw_cost():
    from pathlib import Path

    text = Path(
        "app/services/inventory_costing.py"
    ).read_text()

    assert (
        "raw_cost = await process_moving_average_issue("
        in text
    )

    assert (
        "effective_issue_value"
        not in text
    )


def test_warehouse_handler_passes_posting_actor():
    from pathlib import Path

    text = Path(
        "app/services/warehouse_posting_handler.py"
    ).read_text()

    assert (
        "created_by=context.created_by"
        in text
    )


def test_production_issue_posts_created_peer_replay_events_after_reconciliation():
    """
    Production global order:

        base MA ISSUE + ICE
        -> PVC topology reconciliation
        -> normal document JournalEntry
        -> typed PVC MA JournalEntries.

    The historical ISSUE accounting destination exists before the
    issued PVC MA journal resolver runs.
    """
    from pathlib import Path

    inventory_source = Path(
        "app/services/inventory_costing.py"
    ).read_text()

    integration_source = Path(
        "app/services/"
        "purchase_value_correction_moving_average_"
        "issue_integration_service.py"
    ).read_text()

    warehouse_source = Path(
        "app/services/warehouse_posting_handler.py"
    ).read_text()

    post_source = Path(
        "app/services/"
        "purchase_value_correction_moving_average_"
        "post_accounting_handler.py"
    ).read_text()

    registry_source = Path(
        "app/services/posting_registry.py"
    ).read_text()

    reconcile_call = (
        "await reconcile_purchase_value_correction_"
        "moving_average_after_issue("
    )

    lifecycle_call = (
        "await post_created_purchase_value_correction_"
        "moving_average_peer_journals("
    )

    assert reconcile_call not in inventory_source
    assert reconcile_call in warehouse_source

    assert lifecycle_call not in integration_source
    assert lifecycle_call not in warehouse_source
    assert lifecycle_call in post_source

    assert (
        registry_source.index(
            "WarehousePostingHandler()"
        )
        < registry_source.index(
            "AccountingPostingHandler()"
        )
        < registry_source.index(
            "PurchaseValueCorrectionMovingAverage"
            "PostAccountingHandler()"
        )
    )

    assert "context.get_journal_entry()" in post_source

    assert "AccountingAccountRole.GOODS_COGS" not in post_source
    assert "902" not in post_source

    assert ".commit(" not in integration_source
    assert ".rollback(" not in integration_source
    assert ".commit(" not in post_source
    assert ".rollback(" not in post_source
