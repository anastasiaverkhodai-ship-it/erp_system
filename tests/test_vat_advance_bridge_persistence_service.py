from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.vat_advance_bridge_calculation_service import (
    VatAdvanceBridgeDataIntegrityError,
    VatAdvanceBridgeTarget,
)
from app.services.vat_advance_bridge_persistence_service import (
    build_current_vat_advance_bridge_targets,
    build_vat_advance_bridge_source_plan,
)


D1 = date(
    2026,
    9,
    2,
)

D2 = date(
    2026,
    9,
    3,
)


def _event(
    *,
    event_id: int,
    source_id: int = 10,
    tax_calculation_id: int = 20,
    amount: str = "6.67",
    event_date=D1,
    currency="UAH",
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        tax_calculation_id=(
            tax_calculation_id
        ),
        invoice_fulfillment_allocation_id=(
            source_id
        ),
        bridge_date=event_date,
        bridged_tax_amount=Decimal(
            amount
        ),
        currency_code=currency,
        reversal_of_id=reversal_of_id,
    )


def _target(
    *,
    source_id: int = 10,
    tax_calculation_id: int = 20,
    amount: str = "6.67",
    event_date=D1,
    currency="UAH",
):
    return VatAdvanceBridgeTarget(
        tax_calculation_id=(
            tax_calculation_id
        ),
        source_id=source_id,
        event_date=event_date,
        amount=Decimal(
            amount
        ),
        currency_code=currency,
    )


def test_empty_history_has_no_current_targets():
    assert (
        build_current_vat_advance_bridge_targets(
            events=(),
            currency_code="UAH",
        )
        == ()
    )


def test_reversed_original_is_not_current():
    events = (
        _event(
            event_id=1,
        ),
        _event(
            event_id=2,
            reversal_of_id=1,
            event_date=D2,
        ),
    )

    assert (
        build_current_vat_advance_bridge_targets(
            events=events,
            currency_code="UAH",
        )
        == ()
    )


def test_current_targets_use_active_original():
    events = (
        _event(
            event_id=1,
            source_id=20,
            tax_calculation_id=30,
            amount="5.00",
            event_date=D2,
        ),
        _event(
            event_id=2,
            source_id=10,
            tax_calculation_id=20,
            amount="6.67",
            event_date=D1,
        ),
    )

    targets = (
        build_current_vat_advance_bridge_targets(
            events=events,
            currency_code="UAH",
        )
    )

    assert [
        target.source_id
        for target in targets
    ] == [
        10,
        20,
    ]

    assert [
        target.tax_calculation_id
        for target in targets
    ] == [
        20,
        30,
    ]


def test_multiple_active_originals_fail_closed():
    events = (
        _event(
            event_id=1,
        ),
        _event(
            event_id=2,
            amount="0.01",
        ),
    )

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="more than one active original",
    ):
        build_current_vat_advance_bridge_targets(
            events=events,
            currency_code="UAH",
        )


def test_multiple_tax_calculations_for_same_active_source_fail():
    events = (
        _event(
            event_id=1,
            tax_calculation_id=20,
        ),
        _event(
            event_id=2,
            tax_calculation_id=21,
        ),
    )

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="more than one active original",
    ):
        build_current_vat_advance_bridge_targets(
            events=events,
            currency_code="UAH",
        )


def test_currency_mismatch_fails_closed():
    events = (
        _event(
            event_id=1,
            currency="EUR",
        ),
    )

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="currency does not match",
    ):
        build_current_vat_advance_bridge_targets(
            events=events,
            currency_code="UAH",
        )


def test_nonpositive_active_amount_fails_closed():
    events = (
        _event(
            event_id=1,
            amount="0.00",
        ),
    )

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="must be greater than zero",
    ):
        build_current_vat_advance_bridge_targets(
            events=events,
            currency_code="UAH",
        )


def test_new_positive_source_creates_full_target():
    target = _target()

    plan = (
        build_vat_advance_bridge_source_plan(
            events=(),
            target=target,
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == ()
    assert (
        plan.replacement_target
        == target
    )
    assert plan.is_noop is False


def test_exact_target_is_noop():
    events = (
        _event(
            event_id=1,
        ),
    )

    plan = (
        build_vat_advance_bridge_source_plan(
            events=events,
            target=_target(),
            currency_code="UAH",
        )
    )

    assert plan.is_noop is True


def test_amount_increase_reverses_and_replaces():
    events = (
        _event(
            event_id=1,
            amount="6.67",
        ),
    )

    target = _target(
        amount="10.00",
    )

    plan = (
        build_vat_advance_bridge_source_plan(
            events=events,
            target=target,
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == (
        1,
    )
    assert (
        plan.replacement_target
        == target
    )


def test_amount_decrease_reverses_and_replaces():
    events = (
        _event(
            event_id=1,
            amount="10.00",
        ),
    )

    target = _target(
        amount="6.67",
    )

    plan = (
        build_vat_advance_bridge_source_plan(
            events=events,
            target=target,
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == (
        1,
    )
    assert (
        plan.replacement_target
        == target
    )


def test_zero_target_reverses_without_replacement():
    events = (
        _event(
            event_id=1,
        ),
    )

    plan = (
        build_vat_advance_bridge_source_plan(
            events=events,
            target=_target(
                amount="0.00",
            ),
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == (
        1,
    )
    assert (
        plan.replacement_target
        is None
    )


def test_zero_target_without_current_source_is_noop():
    plan = (
        build_vat_advance_bridge_source_plan(
            events=(),
            target=_target(
                amount="0.00",
            ),
            currency_code="UAH",
        )
    )

    assert plan.is_noop is True


def test_existing_source_event_date_cannot_change():
    events = (
        _event(
            event_id=1,
        ),
    )

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="event_date changed unexpectedly",
    ):
        build_vat_advance_bridge_source_plan(
            events=events,
            target=_target(
                event_date=D2,
            ),
            currency_code="UAH",
        )


def test_existing_source_tax_calculation_cannot_change():
    events = (
        _event(
            event_id=1,
            tax_calculation_id=20,
        ),
    )

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match=(
            "tax_calculation_id changed unexpectedly"
        ),
    ):
        build_vat_advance_bridge_source_plan(
            events=events,
            target=_target(
                tax_calculation_id=21,
            ),
            currency_code="UAH",
        )


def test_reversed_history_can_be_replaced_cleanly():
    events = (
        _event(
            event_id=1,
            amount="6.67",
        ),
        _event(
            event_id=2,
            amount="6.67",
            event_date=D2,
            reversal_of_id=1,
        ),
    )

    target = _target(
        amount="10.00",
    )

    plan = (
        build_vat_advance_bridge_source_plan(
            events=events,
            target=target,
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == ()
    assert (
        plan.replacement_target
        == target
    )


def test_target_currency_mismatch_fails_closed():
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="target currency",
    ):
        build_vat_advance_bridge_source_plan(
            events=(),
            target=_target(
                currency="EUR",
            ),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    "currency_code",
    [
        "",
        "UA",
        "UAHH",
        None,
    ],
)
def test_invalid_reconciliation_currency_fails_closed(
    currency_code,
):
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="currency_code",
    ):
        build_current_vat_advance_bridge_targets(
            events=(),
            currency_code=currency_code,
        )
