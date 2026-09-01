import pytest

from app.services.accounting_reversal import (
    AccountingReversalError,
    _resolve_reversal_tax_recognition_event_id,
)


def test_tax_source_is_preserved_without_override():
    assert (
        _resolve_reversal_tax_recognition_event_id(
            original_tax_recognition_event_id=10,
            override=None,
        )
        == 10
    )


def test_none_tax_source_is_preserved_without_override():
    assert (
        _resolve_reversal_tax_recognition_event_id(
            original_tax_recognition_event_id=None,
            override=None,
        )
        is None
    )


def test_tax_source_can_be_overridden_for_reversal_event():
    assert (
        _resolve_reversal_tax_recognition_event_id(
            original_tax_recognition_event_id=10,
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
def test_tax_source_override_requires_positive_id(
    override,
):
    with pytest.raises(
        AccountingReversalError
    ):
        _resolve_reversal_tax_recognition_event_id(
            original_tax_recognition_event_id=10,
            override=override,
        )


def test_tax_source_override_requires_original_tax_source():
    with pytest.raises(
        AccountingReversalError
    ):
        _resolve_reversal_tax_recognition_event_id(
            original_tax_recognition_event_id=None,
            override=20,
        )
