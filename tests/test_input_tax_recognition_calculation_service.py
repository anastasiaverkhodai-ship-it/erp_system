from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.input_tax_recognition_calculation_service import (
    InputTaxRecognitionCandidateError,
    InputTaxRecognitionDataIntegrityError,
    InputTaxRecognitionDirectionError,
    InputTaxRecognitionMethodError,
    calculate_input_tax_recognition_limit,
)
from app.services.tax_credit_evidence_types import (
    TaxCreditEvidenceType,
)
from app.services.tax_recognition_orchestration_service import (
    TaxRecognitionCandidate,
    TaxRecognitionCandidateKind,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


D1 = date(2026, 8, 10)
D2 = date(2026, 8, 15)
D3 = date(2026, 8, 20)


def calculation(
    *,
    direction=TaxDirection.INPUT,
    method=TaxRecognitionMethod.FIRST_EVENT,
):
    return SimpleNamespace(
        id=10,
        company_id=1,
        tax_type=TaxType.VAT,
        direction=direction,
        recognition_method=method,
        taxable_base=Decimal("100.00"),
        tax_amount=Decimal("20.00"),
        currency_code="UAH",
    )


def candidate(
    *,
    kind,
    source_id,
    event_date,
    base,
    tax,
):
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


def evidence(
    *,
    event_id,
    number,
    effective_date,
    base,
    tax,
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        tax_calculation_id=10,
        evidence_type=(
            TaxCreditEvidenceType
            .REGISTERED_TAX_INVOICE
            .value
        ),
        evidence_number=number,
        evidence_date=D1,
        credit_available_date=D1,
        effective_date=effective_date,
        evidenced_taxable_base=Decimal(
            base
        ),
        evidenced_tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def full_evidence():
    return (
        evidence(
            event_id=1,
            number="PN-1",
            effective_date=D1,
            base="100.00",
            tax="20.00",
        ),
    )


def full_fulfillment():
    return (
        candidate(
            kind=(
                TaxRecognitionCandidateKind
                .FULFILLMENT
            ),
            source_id=101,
            event_date=D1,
            base="100.00",
            tax="20.00",
        ),
    )


def test_evidence_without_economic_event_recognizes_zero():
    result = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=(),
        evidence_events=full_evidence(),
        as_of_date=D1,
    )

    assert result.evidence_tax_amount == Decimal(
        "20.00"
    )
    assert result.economic_tax_amount == Decimal(
        "0"
    )
    assert result.recognizable_tax_amount == Decimal(
        "0"
    )


def test_economic_event_without_evidence_recognizes_zero():
    result = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=full_fulfillment(),
        evidence_events=(),
        as_of_date=D1,
    )

    assert result.economic_tax_amount == Decimal(
        "20.00"
    )
    assert result.evidence_tax_amount == Decimal(
        "0"
    )
    assert result.recognizable_tax_amount == Decimal(
        "0"
    )


def test_full_first_event_plus_full_evidence_recognizes_full():
    result = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=full_fulfillment(),
        evidence_events=full_evidence(),
        as_of_date=D1,
    )

    assert (
        result.recognizable_taxable_base
        == Decimal("100.00")
    )
    assert (
        result.recognizable_tax_amount
        == Decimal("20.00")
    )


def test_partial_evidence_limits_full_economic_capacity():
    result = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=full_fulfillment(),
        evidence_events=(
            evidence(
                event_id=1,
                number="PN-1",
                effective_date=D1,
                base="50.00",
                tax="10.00",
            ),
        ),
        as_of_date=D1,
    )

    assert (
        result.economic_tax_amount
        == Decimal("20.00")
    )
    assert (
        result.evidence_tax_amount
        == Decimal("10.00")
    )
    assert (
        result.recognizable_tax_amount
        == Decimal("10.00")
    )


def test_partial_economic_event_limits_full_evidence():
    result = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=(
            candidate(
                kind=(
                    TaxRecognitionCandidateKind
                    .FULFILLMENT
                ),
                source_id=101,
                event_date=D1,
                base="40.00",
                tax="8.00",
            ),
        ),
        evidence_events=full_evidence(),
        as_of_date=D1,
    )

    assert (
        result.recognizable_taxable_base
        == Decimal("40.00")
    )
    assert (
        result.recognizable_tax_amount
        == Decimal("8.00")
    )


def test_future_evidence_is_not_available_early():
    events = (
        evidence(
            event_id=1,
            number="PN-1",
            effective_date=D2,
            base="100.00",
            tax="20.00",
        ),
    )

    before = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=full_fulfillment(),
        evidence_events=events,
        as_of_date=D1,
    )

    on_date = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=full_fulfillment(),
        evidence_events=events,
        as_of_date=D2,
    )

    assert before.recognizable_tax_amount == ZERO
    assert (
        on_date.recognizable_tax_amount
        == Decimal("20.00")
    )


ZERO = Decimal("0")


def test_future_economic_event_is_not_available_early():
    economic = (
        candidate(
            kind=(
                TaxRecognitionCandidateKind
                .FULFILLMENT
            ),
            source_id=101,
            event_date=D2,
            base="100.00",
            tax="20.00",
        ),
    )

    before = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=economic,
        evidence_events=full_evidence(),
        as_of_date=D1,
    )

    on_date = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=economic,
        evidence_events=full_evidence(),
        as_of_date=D2,
    )

    assert before.recognizable_tax_amount == ZERO
    assert (
        on_date.recognizable_tax_amount
        == Decimal("20.00")
    )


def test_evidence_reversal_removes_capacity_on_reversal_date():
    original = evidence(
        event_id=1,
        number="PN-1",
        effective_date=D1,
        base="100.00",
        tax="20.00",
    )

    reversal = evidence(
        event_id=2,
        number="PN-1",
        effective_date=D2,
        base="100.00",
        tax="20.00",
        reversal_of_id=1,
    )

    before = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=full_fulfillment(),
        evidence_events=(
            original,
            reversal,
        ),
        as_of_date=D1,
    )

    after = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=full_fulfillment(),
        evidence_events=(
            original,
            reversal,
        ),
        as_of_date=D2,
    )

    assert (
        before.recognizable_tax_amount
        == Decimal("20.00")
    )
    assert after.recognizable_tax_amount == ZERO


def test_replacement_evidence_can_take_over_same_day():
    original = evidence(
        event_id=1,
        number="PN-A",
        effective_date=D1,
        base="100.00",
        tax="20.00",
    )

    reversal = evidence(
        event_id=2,
        number="PN-A",
        effective_date=D2,
        base="100.00",
        tax="20.00",
        reversal_of_id=1,
    )

    replacement = evidence(
        event_id=3,
        number="PN-B",
        effective_date=D2,
        base="100.00",
        tax="20.00",
    )

    result = calculate_input_tax_recognition_limit(
        calculation=calculation(),
        economic_candidates=full_fulfillment(),
        evidence_events=(
            original,
            reversal,
            replacement,
        ),
        as_of_date=D2,
    )

    assert (
        result.recognizable_tax_amount
        == Decimal("20.00")
    )


def test_cash_method_ignores_fulfillment():
    economic = (
        candidate(
            kind=(
                TaxRecognitionCandidateKind
                .FULFILLMENT
            ),
            source_id=101,
            event_date=D1,
            base="100.00",
            tax="20.00",
        ),
    )

    result = calculate_input_tax_recognition_limit(
        calculation=calculation(
            method=(
                TaxRecognitionMethod
                .CASH_METHOD
            )
        ),
        economic_candidates=economic,
        evidence_events=full_evidence(),
        as_of_date=D1,
    )

    assert result.economic_tax_amount == ZERO
    assert result.recognizable_tax_amount == ZERO


def test_cash_method_uses_settlement_capacity():
    economic = (
        candidate(
            kind=(
                TaxRecognitionCandidateKind
                .FULFILLMENT
            ),
            source_id=101,
            event_date=D1,
            base="100.00",
            tax="20.00",
        ),
        candidate(
            kind=(
                TaxRecognitionCandidateKind
                .SETTLEMENT
            ),
            source_id=201,
            event_date=D2,
            base="50.00",
            tax="10.00",
        ),
    )

    result = calculate_input_tax_recognition_limit(
        calculation=calculation(
            method=(
                TaxRecognitionMethod
                .CASH_METHOD
            )
        ),
        economic_candidates=economic,
        evidence_events=full_evidence(),
        as_of_date=D2,
    )

    assert (
        result.economic_tax_amount
        == Decimal("10.00")
    )
    assert (
        result.recognizable_tax_amount
        == Decimal("10.00")
    )


def test_output_calculation_is_rejected():
    with pytest.raises(
        InputTaxRecognitionDirectionError
    ):
        calculate_input_tax_recognition_limit(
            calculation=calculation(
                direction=TaxDirection.OUTPUT
            ),
            economic_candidates=(),
            evidence_events=(),
            as_of_date=D1,
        )


def test_manual_calculation_is_rejected():
    with pytest.raises(
        InputTaxRecognitionMethodError
    ):
        calculate_input_tax_recognition_limit(
            calculation=calculation(
                method=TaxRecognitionMethod.MANUAL
            ),
            economic_candidates=(),
            evidence_events=(),
            as_of_date=D1,
        )


def test_duplicate_economic_source_fails_closed():
    item = candidate(
        kind=(
            TaxRecognitionCandidateKind
            .FULFILLMENT
        ),
        source_id=101,
        event_date=D1,
        base="50.00",
        tax="10.00",
    )

    with pytest.raises(
        InputTaxRecognitionCandidateError
    ):
        calculate_input_tax_recognition_limit(
            calculation=calculation(),
            economic_candidates=(
                item,
                item,
            ),
            evidence_events=full_evidence(),
            as_of_date=D1,
        )


def test_evidence_company_mismatch_fails_closed():
    item = evidence(
        event_id=1,
        number="PN-1",
        effective_date=D1,
        base="100.00",
        tax="20.00",
    )

    item.company_id = 2

    with pytest.raises(
        InputTaxRecognitionDataIntegrityError
    ):
        calculate_input_tax_recognition_limit(
            calculation=calculation(),
            economic_candidates=full_fulfillment(),
            evidence_events=(item,),
            as_of_date=D1,
        )


def test_evidence_currency_mismatch_fails_closed():
    item = evidence(
        event_id=1,
        number="PN-1",
        effective_date=D1,
        base="100.00",
        tax="20.00",
    )

    item.currency_code = "EUR"

    with pytest.raises(
        InputTaxRecognitionDataIntegrityError
    ):
        calculate_input_tax_recognition_limit(
            calculation=calculation(),
            economic_candidates=full_fulfillment(),
            evidence_events=(item,),
            as_of_date=D1,
        )
