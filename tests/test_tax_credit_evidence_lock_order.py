import inspect

from app.services.tax_credit_evidence_persistence_service import (
    reverse_tax_credit_evidence,
)


def test_reverse_evidence_uses_consistent_lock_order():
    source = inspect.getsource(
        reverse_tax_credit_evidence
    )

    assert (
        "identity_tax_calculation_id"
        in source
    )

    calculation_position = source.index(
        "await _load_locked_calculation("
    )

    history_position = source.index(
        "await _load_locked_history("
    )

    assert (
        calculation_position
        < history_position
    )

    assert (
        ".with_for_update()"
        not in source
    )


def test_reverse_evidence_reuses_locked_history_for_duplicate_check():
    source = inspect.getsource(
        reverse_tax_credit_evidence
    )

    assert (
        "for event in history"
        in source
    )

    assert (
        "event.reversal_of_id"
        in source
    )
