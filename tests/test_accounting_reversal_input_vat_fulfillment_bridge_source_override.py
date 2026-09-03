import pytest

from app.services.accounting_reversal import (
    AccountingReversalError,
    _resolve_reversal_input_vat_fulfillment_bridge_event_id,
)


def test_input_vat_bridge_source_is_preserved_without_override():
    assert (
        _resolve_reversal_input_vat_fulfillment_bridge_event_id(
            original_input_vat_fulfillment_bridge_event_id=11,
            override=None,
        )
        == 11
    )


def test_input_vat_bridge_source_can_be_overridden():
    assert (
        _resolve_reversal_input_vat_fulfillment_bridge_event_id(
            original_input_vat_fulfillment_bridge_event_id=11,
            override=12,
        )
        == 12
    )


@pytest.mark.parametrize(
    "override",
    [
        0,
        -1,
    ],
)
def test_input_vat_bridge_override_must_be_positive(
    override,
):
    with pytest.raises(
        AccountingReversalError,
        match="must be greater than zero",
    ):
        _resolve_reversal_input_vat_fulfillment_bridge_event_id(
            original_input_vat_fulfillment_bridge_event_id=11,
            override=override,
        )


def test_input_vat_bridge_override_requires_original_source():
    with pytest.raises(
        AccountingReversalError,
        match="requires an original",
    ):
        _resolve_reversal_input_vat_fulfillment_bridge_event_id(
            original_input_vat_fulfillment_bridge_event_id=None,
            override=12,
        )
