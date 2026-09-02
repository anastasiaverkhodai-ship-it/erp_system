from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.input_tax_recognition_reconciliation_service as reconciliation_service

from app.services.input_tax_recognition_persistence_service import (
    InputTaxRecognitionPersistenceTarget,
)
from app.services.input_tax_recognition_reconciliation_service import (
    InputTaxRecognitionReconciliationIntegrityError,
    InputTaxRecognitionReconciliationStateError,
    build_current_input_tax_recognition_targets,
    order_input_tax_recognition_reconciliations,
)


D1 = date(2026, 8, 10)
D2 = date(2026, 8, 20)
D3 = date(2026, 8, 25)


def target(
    *,
    source_id,
    recognition_date=D1,
    base,
    tax,
):
    return InputTaxRecognitionPersistenceTarget(
        tax_credit_evidence_id=(
            source_id
        ),
        recognition_date=(
            recognition_date
        ),
        taxable_base=Decimal(
            base
        ),
        tax_amount=Decimal(
            tax
        ),
    )


def event(
    *,
    event_id,
    source_id,
    recognition_date=D1,
    base,
    tax,
    reversal_of_id=None,
    fulfillment_id=None,
    settlement_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        tax_calculation_id=10,
        invoice_fulfillment_allocation_id=(
            fulfillment_id
        ),
        payment_settlement_allocation_id=(
            settlement_id
        ),
        tax_credit_evidence_id=(
            source_id
        ),
        recognition_date=(
            recognition_date
        ),
        recognized_taxable_base=Decimal(
            base
        ),
        recognized_tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
        reversal_of_id=(
            reversal_of_id
        ),
    )


def test_current_target_from_active_input_event():
    result = (
        build_current_input_tax_recognition_targets(
            (
                event(
                    event_id=1,
                    source_id=101,
                    base="50.00",
                    tax="10.00",
                ),
            )
        )
    )

    assert len(result) == 1

    assert (
        result[0].tax_credit_evidence_id
        == 101
    )

    assert (
        result[0].tax_amount
        == Decimal("10.00")
    )


def test_reversed_original_is_not_current():
    original = event(
        event_id=1,
        source_id=101,
        base="50.00",
        tax="10.00",
    )

    reversal = event(
        event_id=2,
        source_id=101,
        recognition_date=D2,
        base="50.00",
        tax="10.00",
        reversal_of_id=1,
    )

    result = (
        build_current_input_tax_recognition_targets(
            (
                original,
                reversal,
            )
        )
    )

    assert result == ()


def test_multiple_active_events_for_same_evidence_fail():
    with pytest.raises(
        InputTaxRecognitionReconciliationIntegrityError
    ):
        build_current_input_tax_recognition_targets(
            (
                event(
                    event_id=1,
                    source_id=101,
                    base="20.00",
                    tax="4.00",
                ),
                event(
                    event_id=2,
                    source_id=101,
                    base="30.00",
                    tax="6.00",
                ),
            )
        )


def test_output_typed_source_on_input_event_fails():
    with pytest.raises(
        InputTaxRecognitionReconciliationIntegrityError
    ):
        build_current_input_tax_recognition_targets(
            (
                event(
                    event_id=1,
                    source_id=101,
                    base="50.00",
                    tax="10.00",
                    fulfillment_id=999,
                ),
            )
        )


def test_missing_evidence_source_fails():
    bad = event(
        event_id=1,
        source_id=101,
        base="50.00",
        tax="10.00",
    )

    bad.tax_credit_evidence_id = None

    with pytest.raises(
        InputTaxRecognitionReconciliationIntegrityError
    ):
        build_current_input_tax_recognition_targets(
            (bad,)
        )


def test_exact_target_requires_no_adjustment():
    current = (
        target(
            source_id=101,
            base="50.00",
            tax="10.00",
        ),
    )

    result = (
        order_input_tax_recognition_reconciliations(
            current_targets=current,
            desired_targets=current,
            adjustment_date=D2,
        )
    )

    assert result == ()


def test_removed_source_becomes_zero_target():
    current = (
        target(
            source_id=101,
            base="50.00",
            tax="10.00",
        ),
    )

    result = (
        order_input_tax_recognition_reconciliations(
            current_targets=current,
            desired_targets=(),
            adjustment_date=D2,
        )
    )

    assert len(result) == 1

    zero = result[0]

    assert (
        zero.tax_credit_evidence_id
        == 101
    )
    assert zero.recognition_date == D1
    assert zero.taxable_base == Decimal("0")
    assert zero.tax_amount == Decimal("0")


def test_new_source_is_increase():
    desired = target(
        source_id=101,
        base="50.00",
        tax="10.00",
    )

    result = (
        order_input_tax_recognition_reconciliations(
            current_targets=(),
            desired_targets=(
                desired,
            ),
            adjustment_date=D2,
        )
    )

    assert result == (
        desired,
    )


def test_decrease_before_increase():
    current = (
        target(
            source_id=101,
            base="100.00",
            tax="20.00",
        ),
    )

    desired_new = target(
        source_id=202,
        recognition_date=D2,
        base="100.00",
        tax="20.00",
    )

    result = (
        order_input_tax_recognition_reconciliations(
            current_targets=current,
            desired_targets=(
                desired_new,
            ),
            adjustment_date=D2,
        )
    )

    assert len(result) == 2

    assert (
        result[0].tax_credit_evidence_id
        == 101
    )
    assert result[0].tax_amount == Decimal("0")

    assert (
        result[1].tax_credit_evidence_id
        == 202
    )
    assert (
        result[1].tax_amount
        == Decimal("20.00")
    )


def test_date_shift_removal_precedes_new_tranches():
    current = (
        target(
            source_id=101,
            recognition_date=D1,
            base="100.00",
            tax="20.00",
        ),
    )

    desired_existing = target(
        source_id=101,
        recognition_date=D2,
        base="50.00",
        tax="10.00",
    )

    desired_new = target(
        source_id=202,
        recognition_date=D2,
        base="50.00",
        tax="10.00",
    )

    result = (
        order_input_tax_recognition_reconciliations(
            current_targets=current,
            desired_targets=(
                desired_existing,
                desired_new,
            ),
            adjustment_date=D2,
        )
    )

    assert len(result) == 3

    assert (
        result[0].tax_credit_evidence_id
        == 101
    )
    assert result[0].recognition_date == D1
    assert result[0].tax_amount == Decimal("0")

    assert [
        (
            item.tax_credit_evidence_id,
            item.recognition_date,
            item.tax_amount,
        )
        for item in result[1:]
    ] == [
        (
            101,
            D2,
            Decimal("10.00"),
        ),
        (
            202,
            D2,
            Decimal("10.00"),
        ),
    ]


def test_mixed_increase_decrease_same_source_fails():
    current = (
        target(
            source_id=101,
            base="50.00",
            tax="10.00",
        ),
    )

    desired = (
        target(
            source_id=101,
            recognition_date=D1,
            base="40.00",
            tax="12.00",
        ),
    )

    with pytest.raises(
        InputTaxRecognitionReconciliationStateError
    ):
        order_input_tax_recognition_reconciliations(
            current_targets=current,
            desired_targets=desired,
            adjustment_date=D2,
        )


def test_same_amount_later_date_is_remove_then_add():
    current = (
        target(
            source_id=101,
            recognition_date=D1,
            base="50.00",
            tax="10.00",
        ),
    )

    desired = target(
        source_id=101,
        recognition_date=D2,
        base="50.00",
        tax="10.00",
    )

    result = (
        order_input_tax_recognition_reconciliations(
            current_targets=current,
            desired_targets=(
                desired,
            ),
            adjustment_date=D2,
        )
    )

    assert len(result) == 2

    removed = result[0]

    assert (
        removed.tax_credit_evidence_id
        == 101
    )
    assert removed.recognition_date == D1
    assert removed.taxable_base == Decimal("0")
    assert removed.tax_amount == Decimal("0")

    assert result[1] == desired


def test_multiple_reversals_of_original_fail_closed():
    original = event(
        event_id=1,
        source_id=101,
        base="50.00",
        tax="10.00",
    )

    reversal_one = event(
        event_id=2,
        source_id=101,
        recognition_date=D2,
        base="50.00",
        tax="10.00",
        reversal_of_id=1,
    )

    reversal_two = event(
        event_id=3,
        source_id=101,
        recognition_date=D3,
        base="50.00",
        tax="10.00",
        reversal_of_id=1,
    )

    with pytest.raises(
        InputTaxRecognitionReconciliationIntegrityError
    ):
        build_current_input_tax_recognition_targets(
            (
                original,
                reversal_one,
                reversal_two,
            )
        )

def test_active_sources_wrapper_loads_and_forwards_candidates(
    monkeypatch,
):
    calls = []

    calculation_object = object()
    candidate_one = object()
    candidate_two = object()
    expected_result = object()

    async def fake_lock(
        db,
        *,
        company_id,
        tax_calculation_id,
    ):
        calls.append(
            (
                "lock",
                company_id,
                tax_calculation_id,
            )
        )

        return calculation_object

    async def fake_loader(
        db,
        *,
        calculation,
    ):
        calls.append(
            (
                "load",
                calculation,
            )
        )

        return (
            candidate_one,
            candidate_two,
        )

    async def fake_reconcile(
        db,
        **kwargs,
    ):
        calls.append(
            (
                "reconcile",
                kwargs,
            )
        )

        return expected_result

    monkeypatch.setattr(
        reconciliation_service,
        "_lock_tax_calculation",
        fake_lock,
    )

    monkeypatch.setattr(
        reconciliation_service,
        "load_active_input_tax_recognition_candidates",
        fake_loader,
    )

    monkeypatch.setattr(
        reconciliation_service,
        "reconcile_input_tax_calculation_from_candidates",
        fake_reconcile,
    )

    import asyncio

    result = asyncio.run(
        reconciliation_service
        .reconcile_input_tax_calculation_from_active_sources(
            object(),
            company_id=1,
            tax_calculation_id=10,
            adjustment_date=D2,
            created_by=7,
        )
    )

    assert result is expected_result

    assert calls[0] == (
        "lock",
        1,
        10,
    )

    assert calls[1] == (
        "load",
        calculation_object,
    )

    assert calls[2][0] == "reconcile"

    kwargs = calls[2][1]

    assert kwargs[
        "company_id"
    ] == 1

    assert kwargs[
        "tax_calculation_id"
    ] == 10

    assert kwargs[
        "economic_candidates"
    ] == (
        candidate_one,
        candidate_two,
    )

    assert kwargs[
        "adjustment_date"
    ] == D2

    assert kwargs[
        "created_by"
    ] == 7


def test_active_sources_wrapper_rejects_invalid_date_before_db(
    monkeypatch,
):
    async def unexpected_lock(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "DB lock must not run for invalid date"
        )

    monkeypatch.setattr(
        reconciliation_service,
        "_lock_tax_calculation",
        unexpected_lock,
    )

    import asyncio

    with pytest.raises(
        ValueError,
        match="adjustment_date must be a date",
    ):
        asyncio.run(
            reconciliation_service
            .reconcile_input_tax_calculation_from_active_sources(
                object(),
                company_id=1,
                tax_calculation_id=10,
                adjustment_date="2026-08-20",
                created_by=7,
            )
        )

def test_same_evidence_two_dates_are_distinct_current_tranches():
    result = (
        build_current_input_tax_recognition_targets(
            (
                event(
                    event_id=1,
                    source_id=101,
                    recognition_date=D1,
                    base="50.00",
                    tax="10.00",
                ),
                event(
                    event_id=2,
                    source_id=101,
                    recognition_date=D2,
                    base="50.00",
                    tax="10.00",
                ),
            )
        )
    )

    assert [
        (
            item.tax_credit_evidence_id,
            item.recognition_date,
            item.tax_amount,
        )
        for item in result
    ] == [
        (
            101,
            D1,
            Decimal("10.00"),
        ),
        (
            101,
            D2,
            Decimal("10.00"),
        ),
    ]


def test_same_evidence_same_date_duplicate_fails_closed():
    with pytest.raises(
        InputTaxRecognitionReconciliationIntegrityError
    ):
        build_current_input_tax_recognition_targets(
            (
                event(
                    event_id=1,
                    source_id=101,
                    recognition_date=D1,
                    base="25.00",
                    tax="5.00",
                ),
                event(
                    event_id=2,
                    source_id=101,
                    recognition_date=D1,
                    base="25.00",
                    tax="5.00",
                ),
            )
        )


def test_removed_later_tranche_preserves_earlier_tranche():
    current = (
        target(
            source_id=101,
            recognition_date=D1,
            base="50.00",
            tax="10.00",
        ),
        target(
            source_id=101,
            recognition_date=D2,
            base="50.00",
            tax="10.00",
        ),
    )

    desired = (
        target(
            source_id=101,
            recognition_date=D1,
            base="50.00",
            tax="10.00",
        ),
    )

    result = (
        order_input_tax_recognition_reconciliations(
            current_targets=current,
            desired_targets=desired,
            adjustment_date=D3,
        )
    )

    assert len(result) == 1

    removed = result[0]

    assert (
        removed.tax_credit_evidence_id
        == 101
    )
    assert removed.recognition_date == D2
    assert removed.taxable_base == Decimal("0")
    assert removed.tax_amount == Decimal("0")


def test_future_desired_tranche_fails_closed():
    with pytest.raises(
        InputTaxRecognitionReconciliationStateError
    ):
        order_input_tax_recognition_reconciliations(
            current_targets=(),
            desired_targets=(
                target(
                    source_id=101,
                    recognition_date=D3,
                    base="50.00",
                    tax="10.00",
                ),
            ),
            adjustment_date=D2,
        )
