import inspect

import pytest

from app.services.accounting_reversal import (
    AccountingReversalError,
    _resolve_reversal_vat_advance_bridge_event_id,
    reverse_journal_entry,
)


def test_bridge_source_is_preserved_without_override():
    assert (
        _resolve_reversal_vat_advance_bridge_event_id(
            original_vat_advance_bridge_event_id=17,
            override=None,
        )
        == 17
    )


def test_missing_bridge_source_remains_none_without_override():
    assert (
        _resolve_reversal_vat_advance_bridge_event_id(
            original_vat_advance_bridge_event_id=None,
            override=None,
        )
        is None
    )


def test_bridge_source_can_be_rebound_to_reversal_event():
    assert (
        _resolve_reversal_vat_advance_bridge_event_id(
            original_vat_advance_bridge_event_id=17,
            override=18,
        )
        == 18
    )


@pytest.mark.parametrize(
    "override",
    [
        0,
        -1,
    ],
)
def test_bridge_override_requires_positive_id(
    override,
):
    with pytest.raises(
        AccountingReversalError,
        match="must be greater than zero",
    ):
        _resolve_reversal_vat_advance_bridge_event_id(
            original_vat_advance_bridge_event_id=17,
            override=override,
        )


def test_bridge_override_requires_original_bridge_source():
    with pytest.raises(
        AccountingReversalError,
        match=(
            "requires a VAT Advance Bridge "
            "original journal entry"
        ),
    ):
        _resolve_reversal_vat_advance_bridge_event_id(
            original_vat_advance_bridge_event_id=None,
            override=18,
        )


def test_reverse_journal_entry_exposes_bridge_override_argument():
    signature = inspect.signature(
        reverse_journal_entry
    )

    assert (
        "vat_advance_bridge_event_id_override"
        in signature.parameters
    )

    parameter = signature.parameters[
        "vat_advance_bridge_event_id_override"
    ]

    assert parameter.default is None
