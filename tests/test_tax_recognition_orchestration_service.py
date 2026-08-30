from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.tax_recognition_orchestration_service import (
    DuplicateTaxRecognitionSourceError,
    TaxRecognitionCandidateKind,
    TaxRecognitionSourceTarget,
    build_fulfillment_recognition_candidate,
    build_output_tax_recognition_targets,
    build_settlement_recognition_candidate,
    order_output_tax_reconciliations,
)
from app.services.tax_recognition_persistence_service import (
    TaxRecognitionInputEvidenceRequiredError,
    TaxRecognitionSourceMethodError,
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
    base="100.00",
    tax="20.00",
):
    return SimpleNamespace(
        direction=direction,
        recognition_method=method,
        taxable_base=Decimal(
            base
        ),
        tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
    )


def _candidate(
    *,
    kind,
    source_id,
    event_date,
    base,
    tax,
):
    from app.services.tax_recognition_orchestration_service import (
        TaxRecognitionCandidate,
    )

    return TaxRecognitionCandidate(
        kind=kind,
        source_id=source_id,
        event_date=event_date,
        taxable_base_capacity=Decimal(
            base
        ),
        tax_amount_capacity=Decimal(
            tax
        ),
    )


def test_fulfillment_capacity_is_proportional():
    candidate = (
        build_fulfillment_recognition_candidate(
            calculation=_calculation(),
            source_id=10,
            event_date=date(
                2026,
                8,
                20,
            ),
            allocation_quantity=Decimal(
                "2"
            ),
            invoice_line_quantity=Decimal(
                "4"
            ),
        )
    )

    assert (
        candidate.taxable_base_capacity
        == Decimal("50.00")
    )

    assert (
        candidate.tax_amount_capacity
        == Decimal("10.00")
    )


def test_settlement_capacity_uses_full_invoice_ratio():
    candidate = (
        build_settlement_recognition_candidate(
            calculation=_calculation(),
            source_id=20,
            event_date=date(
                2026,
                8,
                20,
            ),
            settlement_amount=Decimal(
                "60.00"
            ),
            invoice_total_amount=Decimal(
                "120.00"
            ),
        )
    )

    assert (
        candidate.taxable_base_capacity
        == Decimal("50.00")
    )

    assert (
        candidate.tax_amount_capacity
        == Decimal("10.00")
    )


def test_payment_first_then_fulfillment_uses_only_remainder():
    calculation = _calculation()

    payment = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .SETTLEMENT
        ),
        source_id=20,
        event_date=date(
            2026,
            8,
            10,
        ),
        base="60.00",
        tax="12.00",
    )

    fulfillment = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .FULFILLMENT
        ),
        source_id=10,
        event_date=date(
            2026,
            8,
            11,
        ),
        base="70.00",
        tax="14.00",
    )

    targets = (
        build_output_tax_recognition_targets(
            calculation=calculation,
            candidates=(
                fulfillment,
                payment,
            ),
        )
    )

    assert [
        (
            target.kind,
            target.source_id,
            target.taxable_base,
            target.tax_amount,
        )
        for target
        in targets
    ] == [
        (
            TaxRecognitionCandidateKind
            .SETTLEMENT,
            20,
            Decimal("60.00"),
            Decimal("12.00"),
        ),
        (
            TaxRecognitionCandidateKind
            .FULFILLMENT,
            10,
            Decimal("40.00"),
            Decimal("8.00"),
        ),
    ]


def test_fulfillment_first_then_payment_uses_remainder():
    calculation = _calculation()

    fulfillment = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .FULFILLMENT
        ),
        source_id=10,
        event_date=date(
            2026,
            8,
            10,
        ),
        base="70.00",
        tax="14.00",
    )

    payment = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .SETTLEMENT
        ),
        source_id=20,
        event_date=date(
            2026,
            8,
            11,
        ),
        base="60.00",
        tax="12.00",
    )

    targets = (
        build_output_tax_recognition_targets(
            calculation=calculation,
            candidates=(
                payment,
                fulfillment,
            ),
        )
    )

    assert [
        (
            target.kind,
            target.source_id,
            target.taxable_base,
            target.tax_amount,
        )
        for target
        in targets
    ] == [
        (
            TaxRecognitionCandidateKind
            .FULFILLMENT,
            10,
            Decimal("70.00"),
            Decimal("14.00"),
        ),
        (
            TaxRecognitionCandidateKind
            .SETTLEMENT,
            20,
            Decimal("30.00"),
            Decimal("6.00"),
        ),
    ]


def test_first_event_is_input_order_independent():
    calculation = _calculation()

    first = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .SETTLEMENT
        ),
        source_id=20,
        event_date=date(
            2026,
            8,
            10,
        ),
        base="60",
        tax="12",
    )

    second = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .FULFILLMENT
        ),
        source_id=10,
        event_date=date(
            2026,
            8,
            11,
        ),
        base="70",
        tax="14",
    )

    left = (
        build_output_tax_recognition_targets(
            calculation=calculation,
            candidates=(
                first,
                second,
            ),
        )
    )

    right = (
        build_output_tax_recognition_targets(
            calculation=calculation,
            candidates=(
                second,
                first,
            ),
        )
    )

    assert left == right


def test_same_date_tie_break_is_deterministic():
    calculation = _calculation()

    same_day = date(
        2026,
        8,
        10,
    )

    fulfillment = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .FULFILLMENT
        ),
        source_id=50,
        event_date=same_day,
        base="100",
        tax="20",
    )

    payment = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .SETTLEMENT
        ),
        source_id=10,
        event_date=same_day,
        base="100",
        tax="20",
    )

    targets = (
        build_output_tax_recognition_targets(
            calculation=calculation,
            candidates=(
                payment,
                fulfillment,
            ),
        )
    )

    assert len(
        targets
    ) == 1

    assert (
        targets[0].kind
        == TaxRecognitionCandidateKind
        .FULFILLMENT
    )

    assert (
        targets[0].event_date
        == same_day
    )


def test_cash_method_ignores_fulfillment():
    calculation = _calculation(
        method=(
            TaxRecognitionMethod
            .CASH_METHOD
        )
    )

    fulfillment = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .FULFILLMENT
        ),
        source_id=10,
        event_date=date(
            2026,
            8,
            1,
        ),
        base="100",
        tax="20",
    )

    payment = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .SETTLEMENT
        ),
        source_id=20,
        event_date=date(
            2026,
            8,
            2,
        ),
        base="50",
        tax="10",
    )

    targets = (
        build_output_tax_recognition_targets(
            calculation=calculation,
            candidates=(
                fulfillment,
                payment,
            ),
        )
    )

    assert len(
        targets
    ) == 1

    assert (
        targets[0].kind
        == TaxRecognitionCandidateKind
        .SETTLEMENT
    )

    assert (
        targets[0].taxable_base
        == Decimal("50")
    )


def test_manual_method_fails_closed():
    calculation = _calculation(
        method=(
            TaxRecognitionMethod.MANUAL
        )
    )

    with pytest.raises(
        TaxRecognitionSourceMethodError
    ):
        build_output_tax_recognition_targets(
            calculation=calculation,
            candidates=(),
        )


def test_input_vat_fails_closed():
    calculation = _calculation(
        direction=(
            TaxDirection.INPUT
        )
    )

    with pytest.raises(
        TaxRecognitionInputEvidenceRequiredError
    ):
        build_output_tax_recognition_targets(
            calculation=calculation,
            candidates=(),
        )


def test_duplicate_source_rejected():
    calculation = _calculation()

    candidate = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .SETTLEMENT
        ),
        source_id=20,
        event_date=date(
            2026,
            8,
            10,
        ),
        base="50",
        tax="10",
    )

    with pytest.raises(
        DuplicateTaxRecognitionSourceError
    ):
        build_output_tax_recognition_targets(
            calculation=calculation,
            candidates=(
                candidate,
                candidate,
            ),
        )


def test_zero_rated_vat_recognizes_base_only():
    calculation = _calculation(
        base="100.00",
        tax="0.00",
    )

    candidate = _candidate(
        kind=(
            TaxRecognitionCandidateKind
            .FULFILLMENT
        ),
        source_id=10,
        event_date=date(
            2026,
            8,
            10,
        ),
        base="60.00",
        tax="0.00",
    )

    targets = (
        build_output_tax_recognition_targets(
            calculation=calculation,
            candidates=(
                candidate,
            ),
        )
    )

    assert (
        targets[0].taxable_base
        == Decimal("60.00")
    )

    assert (
        targets[0].tax_amount
        == Decimal("0.00")
    )


def test_reversal_reallocation_orders_decrease_first():
    current = (
        TaxRecognitionSourceTarget(
            kind=(
                TaxRecognitionCandidateKind
                .SETTLEMENT
            ),
            source_id=20,
            event_date=date(
                2026,
                8,
                10,
            ),
            taxable_base=Decimal(
                "60"
            ),
            tax_amount=Decimal(
                "12"
            ),
        ),
        TaxRecognitionSourceTarget(
            kind=(
                TaxRecognitionCandidateKind
                .FULFILLMENT
            ),
            source_id=10,
            event_date=date(
                2026,
                8,
                11,
            ),
            taxable_base=Decimal(
                "40"
            ),
            tax_amount=Decimal(
                "8"
            ),
        ),
    )

    desired = (
        TaxRecognitionSourceTarget(
            kind=(
                TaxRecognitionCandidateKind
                .FULFILLMENT
            ),
            source_id=10,
            event_date=date(
                2026,
                8,
                11,
            ),
            taxable_base=Decimal(
                "70"
            ),
            tax_amount=Decimal(
                "14"
            ),
        ),
    )

    sequence = (
        order_output_tax_reconciliations(
            current_targets=current,
            desired_targets=desired,
        )
    )

    assert len(
        sequence
    ) == 2

    assert (
        sequence[0].kind
        == TaxRecognitionCandidateKind
        .SETTLEMENT
    )

    assert (
        sequence[0].source_id
        == 20
    )

    assert (
        sequence[0].taxable_base
        == Decimal("0")
    )

    assert (
        sequence[0].tax_amount
        == Decimal("0")
    )

    assert (
        sequence[1].kind
        == TaxRecognitionCandidateKind
        .FULFILLMENT
    )

    assert (
        sequence[1].taxable_base
        == Decimal("70")
    )


def test_unchanged_targets_require_no_reconciliation():
    target = (
        TaxRecognitionSourceTarget(
            kind=(
                TaxRecognitionCandidateKind
                .SETTLEMENT
            ),
            source_id=20,
            event_date=date(
                2026,
                8,
                10,
            ),
            taxable_base=Decimal(
                "50"
            ),
            tax_amount=Decimal(
                "10"
            ),
        )
    )

    sequence = (
        order_output_tax_reconciliations(
            current_targets=(
                target,
            ),
            desired_targets=(
                target,
            ),
        )
    )

    assert sequence == ()
