from pathlib import Path


def test_generic_target_reconciler_reuses_low_level_immutable_persistence():
    path = Path(
        "app/services/"
        "purchase_value_correction_moving_average_"
        "target_reconciliation_service.py"
    )

    text = path.read_text()

    assert (
        "reconcile_purchase_value_correction_"
        "moving_average_replay_source"
        in text
    )

    assert "_load_history_for_update" in text
    assert "_active_originals" in text
    assert "_same_state_except_date" in text
    assert "_removal_target" in text

    assert ".commit(" not in text
    assert ".rollback(" not in text


def test_generic_target_reconciler_is_forward_only():
    path = Path(
        "app/services/"
        "purchase_value_correction_moving_average_"
        "target_reconciliation_service.py"
    )

    text = path.read_text()

    assert "adjustment_date" in text
    assert "reversal_date=adjustment_date" in text
    assert (
        "recognition_date=adjustment_date"
        in text
    )


def test_peer_reconciliation_delegates_to_public_target_engine():
    path = Path(
        "app/services/"
        "purchase_value_correction_moving_average_"
        "peer_reconciliation_service.py"
    )

    text = path.read_text()

    assert (
        "reconcile_purchase_value_correction_"
        "moving_average_targets"
        in text
    )
