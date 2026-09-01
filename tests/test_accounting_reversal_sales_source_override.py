import pytest

from app.services.accounting_reversal import (
    AccountingReversalError,
    _resolve_reversal_sales_recognition_event_id,
)


def test_without_override_preserves_original_sales_source():

    assert (
        _resolve_reversal_sales_recognition_event_id(
            original_sales_recognition_event_id=10,
            override=None,
        )
        == 10
    )


def test_without_override_preserves_none():

    assert (
        _resolve_reversal_sales_recognition_event_id(
            original_sales_recognition_event_id=None,
            override=None,
        )
        is None
    )


def test_sales_reversal_event_can_override_original_source():

    assert (
        _resolve_reversal_sales_recognition_event_id(
            original_sales_recognition_event_id=10,
            override=20,
        )
        == 20
    )


@pytest.mark.parametrize(
    "override",
    (
        0,
        -1,
    ),
)
def test_invalid_override_fails_closed(
    override,
):

    with pytest.raises(
        AccountingReversalError
    ):
        _resolve_reversal_sales_recognition_event_id(
            original_sales_recognition_event_id=10,
            override=override,
        )


def test_override_requires_original_sales_source():

    with pytest.raises(
        AccountingReversalError
    ):
        _resolve_reversal_sales_recognition_event_id(
            original_sales_recognition_event_id=None,
            override=20,
        )
