from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.sales_recognition_calculation_service import (
    SalesRecognitionDataIntegrityError,
    SalesRecognitionTarget,
)
from app.services.sales_recognition_persistence_service import (
    build_current_sales_recognition_targets,
    build_sales_recognition_source_plan,
)


D1 = date(2026, 9, 1)

D2 = date(2026, 9, 2)


def _event(
    *,
    event_id: int,
    source_id: int,
    quantity: str,
    gross: str,
    tax: str,
    event_date=D1,
    currency="UAH",
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        invoice_fulfillment_allocation_id=(
            source_id
        ),
        recognition_date=event_date,
        recognized_quantity=Decimal(
            quantity
        ),
        recognized_gross_amount=Decimal(
            gross
        ),
        recognized_tax_amount=Decimal(
            tax
        ),
        currency_code=currency,
        reversal_of_id=reversal_of_id,
    )


def _target(
    *,
    source_id: int = 10,
    quantity: str = "1",
    gross: str = "33.33",
    tax: str = "6.67",
    event_date=D1,
):
    return SalesRecognitionTarget(
        source_id=source_id,
        event_date=event_date,
        quantity=Decimal(
            quantity
        ),
        gross_amount=Decimal(
            gross
        ),
        tax_amount=Decimal(
            tax
        ),
    )


def test_empty_history_has_no_current_targets():
    assert (
        build_current_sales_recognition_targets(
            events=(),
            currency_code="UAH",
        )
        == ()
    )


def test_reversed_original_is_not_current():
    events = (
        _event(
            event_id=1,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
        ),
        _event(
            event_id=2,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
            reversal_of_id=1,
        ),
    )

    assert (
        build_current_sales_recognition_targets(
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
            quantity="1",
            gross="33.34",
            tax="6.66",
            event_date=D2,
        ),
        _event(
            event_id=2,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
            event_date=D1,
        ),
    )

    targets = (
        build_current_sales_recognition_targets(
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


def test_multiple_active_originals_fail_closed():
    events = (
        _event(
            event_id=1,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
        ),
        _event(
            event_id=2,
            source_id=10,
            quantity="0.1",
            gross="0.01",
            tax="0.00",
        ),
    )

    with pytest.raises(
        SalesRecognitionDataIntegrityError,
        match="more than one active original",
    ):
        build_current_sales_recognition_targets(
            events=events,
            currency_code="UAH",
        )


def test_currency_mismatch_fails_closed():
    events = (
        _event(
            event_id=1,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
            currency="EUR",
        ),
    )

    with pytest.raises(
        SalesRecognitionDataIntegrityError,
        match="currency does not match",
    ):
        build_current_sales_recognition_targets(
            events=events,
            currency_code="UAH",
        )


def test_new_source_creates_full_target():
    target = _target()

    plan = (
        build_sales_recognition_source_plan(
            events=(),
            target=target,
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target == target
    assert plan.is_noop is False


def test_exact_target_is_noop():
    events = (
        _event(
            event_id=1,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
        ),
    )

    plan = (
        build_sales_recognition_source_plan(
            events=events,
            target=_target(),
            currency_code="UAH",
        )
    )

    assert plan.is_noop is True


def test_penny_increase_reverses_and_replaces_full_target():
    events = (
        _event(
            event_id=1,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
        ),
    )

    target = _target(
        gross="33.34",
        tax="6.66",
    )

    plan = (
        build_sales_recognition_source_plan(
            events=events,
            target=target,
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == (
        1,
    )
    assert plan.replacement_target == target

    assert (
        plan.replacement_target.quantity
        == Decimal("1")
    )
    assert (
        plan.replacement_target.gross_amount
        == Decimal("33.34")
    )


def test_amount_decrease_reverses_and_replaces_full_target():
    events = (
        _event(
            event_id=1,
            source_id=10,
            quantity="1",
            gross="33.34",
            tax="6.67",
        ),
    )

    target = _target(
        gross="33.33",
        tax="6.66",
    )

    plan = (
        build_sales_recognition_source_plan(
            events=events,
            target=target,
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == (
        1,
    )
    assert plan.replacement_target == target


def test_zero_target_reverses_without_replacement():
    events = (
        _event(
            event_id=1,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
        ),
    )

    zero_target = _target(
        quantity="0",
        gross="0",
        tax="0",
    )

    plan = (
        build_sales_recognition_source_plan(
            events=events,
            target=zero_target,
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == (
        1,
    )
    assert plan.replacement_target is None


def test_zero_target_without_current_source_is_noop():
    zero_target = _target(
        quantity="0",
        gross="0",
        tax="0",
    )

    plan = (
        build_sales_recognition_source_plan(
            events=(),
            target=zero_target,
            currency_code="UAH",
        )
    )

    assert plan.is_noop is True


def test_existing_source_event_date_cannot_change():
    events = (
        _event(
            event_id=1,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
        ),
    )

    with pytest.raises(
        SalesRecognitionDataIntegrityError,
        match="event_date changed unexpectedly",
    ):
        build_sales_recognition_source_plan(
            events=events,
            target=_target(
                event_date=D2,
            ),
            currency_code="UAH",
        )


def test_existing_source_quantity_cannot_change():
    events = (
        _event(
            event_id=1,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
        ),
    )

    with pytest.raises(
        SalesRecognitionDataIntegrityError,
        match="quantity changed unexpectedly",
    ):
        build_sales_recognition_source_plan(
            events=events,
            target=_target(
                quantity="2",
            ),
            currency_code="UAH",
        )


def test_reversed_history_can_be_replaced_cleanly():
    events = (
        _event(
            event_id=1,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
        ),
        _event(
            event_id=2,
            source_id=10,
            quantity="1",
            gross="33.33",
            tax="6.67",
            reversal_of_id=1,
        ),
    )

    target = _target(
        gross="33.34",
        tax="6.66",
    )

    plan = (
        build_sales_recognition_source_plan(
            events=events,
            target=target,
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target == target
