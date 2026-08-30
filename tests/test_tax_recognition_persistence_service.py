from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.tax_recognition_persistence_service import (
    TaxRecognitionInputEvidenceRequiredError,
    TaxRecognitionOverRecognitionError,
    TaxRecognitionSourceError,
    TaxRecognitionSourceMethodError,
    build_tax_recognition_source_plan,
    calculate_tax_recognition_net,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_types import (
    TaxDirection,
)


def _calculation(
    *,
    direction=TaxDirection.OUTPUT,
    method=TaxRecognitionMethod.FIRST_EVENT,
    taxable_base="100.00",
    tax_amount="20.00",
):
    return SimpleNamespace(
        direction=direction,
        recognition_method=method,
        taxable_base=Decimal(
            taxable_base
        ),
        tax_amount=Decimal(
            tax_amount
        ),
        currency_code="UAH",
    )


def _event(
    *,
    event_id: int,
    base: str,
    tax: str,
    fulfillment_id: int | None = None,
    settlement_id: int | None = None,
    reversal_of_id: int | None = None,
):
    return SimpleNamespace(
        id=event_id,
        invoice_fulfillment_allocation_id=(
            fulfillment_id
        ),
        payment_settlement_allocation_id=(
            settlement_id
        ),
        recognized_taxable_base=Decimal(
            base
        ),
        recognized_tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def test_empty_source_appends_target():
    plan = build_tax_recognition_source_plan(
        calculation=_calculation(),
        events=(),
        target_taxable_base=Decimal(
            "60.00"
        ),
        target_tax_amount=Decimal(
            "12.00"
        ),
        invoice_fulfillment_allocation_id=10,
    )

    assert plan.reversal_event_ids == ()

    assert (
        plan.increment_taxable_base
        == Decimal("60.00")
    )

    assert (
        plan.increment_tax_amount
        == Decimal("12.00")
    )


def test_source_increase_appends_delta():
    events = (
        _event(
            event_id=1,
            base="60.00",
            tax="12.00",
            fulfillment_id=10,
        ),
    )

    plan = build_tax_recognition_source_plan(
        calculation=_calculation(),
        events=events,
        target_taxable_base=Decimal(
            "100.00"
        ),
        target_tax_amount=Decimal(
            "20.00"
        ),
        invoice_fulfillment_allocation_id=10,
    )

    assert (
        plan.increment_taxable_base
        == Decimal("40.00")
    )

    assert (
        plan.increment_tax_amount
        == Decimal("8.00")
    )


def test_same_target_is_noop():
    events = (
        _event(
            event_id=1,
            base="100.00",
            tax="20.00",
            fulfillment_id=10,
        ),
    )

    plan = build_tax_recognition_source_plan(
        calculation=_calculation(),
        events=events,
        target_taxable_base=Decimal(
            "100.00"
        ),
        target_tax_amount=Decimal(
            "20.00"
        ),
        invoice_fulfillment_allocation_id=10,
    )

    assert plan.is_noop is True


def test_reduction_reverses_active_history():
    events = (
        _event(
            event_id=1,
            base="60.00",
            tax="12.00",
            fulfillment_id=10,
        ),
        _event(
            event_id=2,
            base="40.00",
            tax="8.00",
            fulfillment_id=10,
        ),
    )

    plan = build_tax_recognition_source_plan(
        calculation=_calculation(),
        events=events,
        target_taxable_base=Decimal(
            "70.00"
        ),
        target_tax_amount=Decimal(
            "14.00"
        ),
        invoice_fulfillment_allocation_id=10,
    )

    assert (
        plan.reversal_event_ids
        == (1, 2)
    )

    assert (
        plan.increment_taxable_base
        == Decimal("70.00")
    )

    assert (
        plan.increment_tax_amount
        == Decimal("14.00")
    )


def test_reversal_removes_original_from_net():
    events = (
        _event(
            event_id=1,
            base="60.00",
            tax="12.00",
            settlement_id=20,
        ),
        _event(
            event_id=2,
            base="60.00",
            tax="12.00",
            settlement_id=20,
            reversal_of_id=1,
        ),
    )

    net = calculate_tax_recognition_net(
        events
    )

    assert (
        net.taxable_base
        == Decimal("0")
    )

    assert (
        net.tax_amount
        == Decimal("0")
    )


def test_other_source_capacity_protected():
    events = (
        _event(
            event_id=1,
            base="60.00",
            tax="12.00",
            settlement_id=20,
        ),
        _event(
            event_id=2,
            base="40.00",
            tax="8.00",
            fulfillment_id=10,
        ),
    )

    with pytest.raises(
        TaxRecognitionOverRecognitionError
    ):
        build_tax_recognition_source_plan(
            calculation=_calculation(),
            events=events,
            target_taxable_base=Decimal(
                "80.00"
            ),
            target_tax_amount=Decimal(
                "16.00"
            ),
            payment_settlement_allocation_id=20,
        )


def test_input_vat_fails_closed():
    with pytest.raises(
        TaxRecognitionInputEvidenceRequiredError
    ):
        build_tax_recognition_source_plan(
            calculation=_calculation(
                direction=TaxDirection.INPUT
            ),
            events=(),
            target_taxable_base=Decimal(
                "100.00"
            ),
            target_tax_amount=Decimal(
                "20.00"
            ),
            payment_settlement_allocation_id=20,
        )


def test_cash_method_rejects_fulfillment():
    with pytest.raises(
        TaxRecognitionSourceMethodError
    ):
        build_tax_recognition_source_plan(
            calculation=_calculation(
                method=(
                    TaxRecognitionMethod
                    .CASH_METHOD
                )
            ),
            events=(),
            target_taxable_base=Decimal(
                "100.00"
            ),
            target_tax_amount=Decimal(
                "20.00"
            ),
            invoice_fulfillment_allocation_id=10,
        )


def test_cash_method_accepts_settlement():
    plan = build_tax_recognition_source_plan(
        calculation=_calculation(
            method=(
                TaxRecognitionMethod
                .CASH_METHOD
            )
        ),
        events=(),
        target_taxable_base=Decimal(
            "50.00"
        ),
        target_tax_amount=Decimal(
            "10.00"
        ),
        payment_settlement_allocation_id=20,
    )

    assert plan.is_noop is False


def test_first_event_accepts_settlement():
    plan = build_tax_recognition_source_plan(
        calculation=_calculation(),
        events=(),
        target_taxable_base=Decimal(
            "30.00"
        ),
        target_tax_amount=Decimal(
            "6.00"
        ),
        payment_settlement_allocation_id=20,
    )

    assert plan.is_noop is False


def test_exactly_one_source_required():
    with pytest.raises(
        TaxRecognitionSourceError
    ):
        build_tax_recognition_source_plan(
            calculation=_calculation(),
            events=(),
            target_taxable_base=Decimal(
                "10.00"
            ),
            target_tax_amount=Decimal(
                "2.00"
            ),
        )

    with pytest.raises(
        TaxRecognitionSourceError
    ):
        build_tax_recognition_source_plan(
            calculation=_calculation(),
            events=(),
            target_taxable_base=Decimal(
                "10.00"
            ),
            target_tax_amount=Decimal(
                "2.00"
            ),
            invoice_fulfillment_allocation_id=10,
            payment_settlement_allocation_id=20,
        )
