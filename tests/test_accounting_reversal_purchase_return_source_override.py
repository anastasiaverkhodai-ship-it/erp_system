import inspect

import pytest

import app.services.accounting_reversal as accounting_reversal

from app.services.accounting_reversal import (
    AccountingReversalError,
    _resolve_reversal_purchase_return_recognition_event_id,
    reverse_journal_entry,
)


def test_preserves_purchase_return_source_by_default():
    assert (
        _resolve_reversal_purchase_return_recognition_event_id(
            original_purchase_return_recognition_event_id=10,
            override=None,
        )
        == 10
    )


def test_none_remains_none_without_override():
    assert (
        _resolve_reversal_purchase_return_recognition_event_id(
            original_purchase_return_recognition_event_id=None,
            override=None,
        )
        is None
    )


def test_reversal_event_can_override_purchase_return_source():
    assert (
        _resolve_reversal_purchase_return_recognition_event_id(
            original_purchase_return_recognition_event_id=10,
            override=11,
        )
        == 11
    )


@pytest.mark.parametrize(
    "override",
    [
        0,
        -1,
    ],
)
def test_override_must_be_positive(
    override,
):
    with pytest.raises(
        AccountingReversalError,
        match="greater than zero",
    ):
        _resolve_reversal_purchase_return_recognition_event_id(
            original_purchase_return_recognition_event_id=10,
            override=override,
        )


def test_override_requires_purchase_return_original():
    with pytest.raises(
        AccountingReversalError,
        match="requires an original",
    ):
        _resolve_reversal_purchase_return_recognition_event_id(
            original_purchase_return_recognition_event_id=None,
            override=11,
        )


def test_reverse_journal_entry_exposes_purchase_return_override():
    signature = inspect.signature(
        reverse_journal_entry
    )

    assert (
        "purchase_return_recognition_event_id_override"
        in signature.parameters
    )


def test_reverse_journal_entry_wires_purchase_return_source():
    source = inspect.getsource(
        accounting_reversal.reverse_journal_entry
    )

    assert (
        "purchase_return_recognition_event_id=("
        in source
    )

    assert (
        "_resolve_reversal_"
        "purchase_return_recognition_event_id"
        in source
    )

    assert (
        "purchase_return_recognition_event_id_override"
        in source
    )
