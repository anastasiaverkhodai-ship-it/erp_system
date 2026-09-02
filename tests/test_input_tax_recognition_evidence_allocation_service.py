from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.input_tax_recognition_evidence_allocation_service import (
    build_input_tax_recognition_evidence_targets,
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
    method=TaxRecognitionMethod.FIRST_EVENT,
):
    return SimpleNamespace(
        id=10,
        company_id=1,
        tax_type=TaxType.VAT,
        direction=TaxDirection.INPUT,
        recognition_method=method,
        taxable_base=Decimal("100.00"),
        tax_amount=Decimal("20.00"),
        currency_code="UAH",
    )


def candidate(
    *,
    kind=(
        TaxRecognitionCandidateKind
        .FULFILLMENT
    ),
    source_id=101,
    event_date=D1,
    base="100.00",
    tax="20.00",
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


def test_no_economic_capacity_produces_no_targets():
    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                    effective_date=D1,
                    base="100.00",
                    tax="20.00",
                ),
            ),
            as_of_date=D1,
        )
    )

    assert result == ()


def test_no_evidence_produces_no_targets():
    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(
                candidate(),
            ),
            evidence_events=(),
            as_of_date=D1,
        )
    )

    assert result == ()


def test_full_evidence_gets_full_target():
    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(
                candidate(),
            ),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                    effective_date=D1,
                    base="100.00",
                    tax="20.00",
                ),
            ),
            as_of_date=D1,
        )
    )

    assert len(result) == 1

    target = result[0]

    assert (
        target.tax_credit_evidence_id
        == 1
    )
    assert target.event_date == D1
    assert (
        target.taxable_base
        == Decimal("100.00")
    )
    assert (
        target.tax_amount
        == Decimal("20.00")
    )


def test_two_evidence_sources_allocate_fifo():
    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(
                candidate(),
            ),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-A",
                    effective_date=D1,
                    base="40.00",
                    tax="8.00",
                ),
                evidence(
                    event_id=2,
                    number="PN-B",
                    effective_date=D1,
                    base="60.00",
                    tax="12.00",
                ),
            ),
            as_of_date=D1,
        )
    )

    assert [
        item.tax_credit_evidence_id
        for item in result
    ] == [
        1,
        2,
    ]

    assert [
        item.tax_amount
        for item in result
    ] == [
        Decimal("8.00"),
        Decimal("12.00"),
    ]


def test_evidence_effective_date_delays_target_date():
    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(
                candidate(
                    event_date=D1,
                ),
            ),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                    effective_date=D2,
                    base="100.00",
                    tax="20.00",
                ),
            ),
            as_of_date=D2,
        )
    )

    assert len(result) == 1
    assert result[0].event_date == D2


def test_economic_event_delays_target_date():
    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(
                candidate(
                    event_date=D2,
                ),
            ),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                    effective_date=D1,
                    base="100.00",
                    tax="20.00",
                ),
            ),
            as_of_date=D2,
        )
    )

    assert len(result) == 1
    assert result[0].event_date == D2


def test_staggered_economic_capacity_sets_per_source_dates():
    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(
                candidate(
                    source_id=101,
                    event_date=D1,
                    base="50.00",
                    tax="10.00",
                ),
                candidate(
                    source_id=102,
                    event_date=D3,
                    base="50.00",
                    tax="10.00",
                ),
            ),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-A",
                    effective_date=D1,
                    base="50.00",
                    tax="10.00",
                ),
                evidence(
                    event_id=2,
                    number="PN-B",
                    effective_date=D1,
                    base="50.00",
                    tax="10.00",
                ),
            ),
            as_of_date=D3,
        )
    )

    assert [
        item.event_date
        for item in result
    ] == [
        D1,
        D3,
    ]


def test_reversed_evidence_is_not_targeted():
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

    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(
                candidate(),
            ),
            evidence_events=(
                original,
                reversal,
            ),
            as_of_date=D2,
        )
    )

    assert result == ()


def test_replacement_evidence_takes_over_same_day():
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

    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(
                candidate(
                    event_date=D1,
                ),
            ),
            evidence_events=(
                original,
                reversal,
                replacement,
            ),
            as_of_date=D2,
        )
    )

    assert len(result) == 1
    assert (
        result[0].tax_credit_evidence_id
        == 3
    )
    assert result[0].event_date == D2
    assert (
        result[0].tax_amount
        == Decimal("20.00")
    )


def test_partial_economic_capacity_limits_evidence_target():
    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(
                candidate(
                    base="50.00",
                    tax="10.00",
                ),
            ),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                    effective_date=D1,
                    base="100.00",
                    tax="20.00",
                ),
            ),
            as_of_date=D1,
        )
    )

    assert len(result) == 1
    assert (
        result[0].taxable_base
        == Decimal("50.00")
    )
    assert (
        result[0].tax_amount
        == Decimal("10.00")
    )


def test_partial_evidence_limits_economic_capacity():
    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(
                candidate(),
            ),
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
    )

    assert len(result) == 1
    assert (
        result[0].taxable_base
        == Decimal("50.00")
    )
    assert (
        result[0].tax_amount
        == Decimal("10.00")
    )


def test_cash_method_uses_settlement_date():
    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(
                method=(
                    TaxRecognitionMethod
                    .CASH_METHOD
                )
            ),
            economic_candidates=(
                candidate(
                    kind=(
                        TaxRecognitionCandidateKind
                        .FULFILLMENT
                    ),
                    source_id=101,
                    event_date=D1,
                ),
                candidate(
                    kind=(
                        TaxRecognitionCandidateKind
                        .SETTLEMENT
                    ),
                    source_id=201,
                    event_date=D2,
                    base="100.00",
                    tax="20.00",
                ),
            ),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                    effective_date=D1,
                    base="100.00",
                    tax="20.00",
                ),
            ),
            as_of_date=D2,
        )
    )

    assert len(result) == 1
    assert result[0].event_date == D2
    assert (
        result[0].tax_amount
        == Decimal("20.00")
    )


def test_one_evidence_preserves_staggered_economic_tranches():
    result = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation(),
            economic_candidates=(
                candidate(
                    source_id=101,
                    event_date=D2,
                    base="50.00",
                    tax="10.00",
                ),
                candidate(
                    source_id=102,
                    event_date=D3,
                    base="50.00",
                    tax="10.00",
                ),
            ),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-ONE-EVIDENCE",
                    effective_date=D1,
                    base="100.00",
                    tax="20.00",
                ),
            ),
            as_of_date=D3,
        )
    )

    assert len(result) == 2

    assert [
        item.tax_credit_evidence_id
        for item in result
    ] == [
        1,
        1,
    ]

    assert [
        item.event_date
        for item in result
    ] == [
        D2,
        D3,
    ]

    assert [
        item.taxable_base
        for item in result
    ] == [
        Decimal("50.00"),
        Decimal("50.00"),
    ]

    assert [
        item.tax_amount
        for item in result
    ] == [
        Decimal("10.00"),
        Decimal("10.00"),
    ]
