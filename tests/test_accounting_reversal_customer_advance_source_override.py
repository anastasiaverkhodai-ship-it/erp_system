import pytest

from app.services.accounting_reversal import (
    AccountingReversalError,
    _resolve_reversal_customer_advance_clearing_event_id,
)


def test_preserves_customer_source_by_default():
    assert (
        _resolve_reversal_customer_advance_clearing_event_id(
            original_customer_advance_clearing_event_id=10,
            override=None,
        )
        == 10
    )


def test_none_remains_none_without_override():
    assert (
        _resolve_reversal_customer_advance_clearing_event_id(
            original_customer_advance_clearing_event_id=None,
            override=None,
        )
        is None
    )


def test_reversal_event_can_override_source():
    assert (
        _resolve_reversal_customer_advance_clearing_event_id(
            original_customer_advance_clearing_event_id=10,
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
        _resolve_reversal_customer_advance_clearing_event_id(
            original_customer_advance_clearing_event_id=10,
            override=override,
        )


def test_override_requires_customer_original():
    with pytest.raises(
        AccountingReversalError,
        match="requires an original",
    ):
        _resolve_reversal_customer_advance_clearing_event_id(
            original_customer_advance_clearing_event_id=None,
            override=11,
        )
