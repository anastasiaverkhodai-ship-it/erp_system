from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.tax_credit_evidence_persistence_service import (
    TaxCreditEvidenceCapacityError,
    TaxCreditEvidenceDataIntegrityError,
    TaxCreditEvidenceDirectionError,
    TaxCreditEvidenceDuplicateError,
    TaxCreditEvidenceValidationError,
    build_tax_credit_evidence_target,
    build_tax_credit_evidence_windows,
    validate_input_vat_tax_calculation,
    validate_new_tax_credit_evidence_against_history,
)
from app.services.tax_credit_evidence_types import (
    TaxCreditEvidenceType,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


D1 = date(
    2026,
    8,
    10,
)

D2 = date(
    2026,
    8,
    15,
)

D3 = date(
    2026,
    8,
    20,
)


def target(
    *,
    number="PN-1",
    available=D1,
    base=Decimal("100.00"),
    tax=Decimal("20.00"),
):
    return build_tax_credit_evidence_target(
        evidence_type=(
            TaxCreditEvidenceType
            .REGISTERED_TAX_INVOICE
        ),
        evidence_number=number,
        evidence_date=D1,
        credit_available_date=available,
        evidenced_taxable_base=base,
        evidenced_tax_amount=tax,
        currency_code="UAH",
    )


def event(
    *,
    event_id,
    number="PN-1",
    effective=D1,
    base=Decimal("100.00"),
    tax=Decimal("20.00"),
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
        effective_date=effective,
        evidenced_taxable_base=base,
        evidenced_tax_amount=tax,
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def calculation(
    *,
    direction=TaxDirection.INPUT,
    currency="UAH",
):
    return SimpleNamespace(
        id=10,
        company_id=1,
        tax_type=TaxType.VAT,
        direction=direction,
        taxable_base=Decimal("100.00"),
        tax_amount=Decimal("20.00"),
        currency_code=currency,
    )


def test_target_normalizes_number_currency_and_money():
    actual = build_tax_credit_evidence_target(
        evidence_type=(
            TaxCreditEvidenceType
            .REGISTERED_TAX_INVOICE
        ),
        evidence_number="  PN-1  ",
        evidence_date=D1,
        credit_available_date=D2,
        evidenced_taxable_base=Decimal("100"),
        evidenced_tax_amount=Decimal("20"),
        currency_code="uah",
    )

    assert actual.evidence_number == "PN-1"
    assert actual.currency_code == "UAH"
    assert (
        actual.evidenced_taxable_base
        == Decimal("100.00")
    )
    assert (
        actual.evidenced_tax_amount
        == Decimal("20.00")
    )
    assert actual.effective_date == D2


def test_available_date_cannot_precede_evidence_date():
    with pytest.raises(
        TaxCreditEvidenceValidationError
    ):
        build_tax_credit_evidence_target(
            evidence_type=(
                TaxCreditEvidenceType
                .REGISTERED_TAX_INVOICE
            ),
            evidence_number="PN-1",
            evidence_date=D2,
            credit_available_date=D1,
            evidenced_taxable_base=Decimal("100"),
            evidenced_tax_amount=Decimal("20"),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    "tax_amount",
    [
        Decimal("0"),
        Decimal("-1"),
    ],
)
def test_evidence_tax_must_be_positive(
    tax_amount,
):
    with pytest.raises(
        TaxCreditEvidenceValidationError
    ):
        target(
            tax=tax_amount
        )


def test_input_vat_calculation_is_accepted():
    validate_input_vat_tax_calculation(
        calculation=calculation(),
        company_id=1,
        target=target(),
    )


def test_output_vat_calculation_is_rejected():
    with pytest.raises(
        TaxCreditEvidenceDirectionError
    ):
        validate_input_vat_tax_calculation(
            calculation=calculation(
                direction=TaxDirection.OUTPUT
            ),
            company_id=1,
            target=target(),
        )


def test_currency_must_match_calculation():
    from app.services.tax_credit_evidence_persistence_service import (
        TaxCreditEvidenceCurrencyError,
    )

    with pytest.raises(
        TaxCreditEvidenceCurrencyError
    ):
        validate_input_vat_tax_calculation(
            calculation=calculation(
                currency="EUR"
            ),
            company_id=1,
            target=target(),
        )


def test_single_evidence_within_capacity_is_valid():
    validate_new_tax_credit_evidence_against_history(
        target=target(),
        history=(),
        calculation_taxable_base=Decimal(
            "100.00"
        ),
        calculation_tax_amount=Decimal(
            "20.00"
        ),
    )


def test_single_evidence_cannot_exceed_tax_capacity():
    with pytest.raises(
        TaxCreditEvidenceCapacityError
    ):
        validate_new_tax_credit_evidence_against_history(
            target=target(
                tax=Decimal("20.01")
            ),
            history=(),
            calculation_taxable_base=Decimal(
                "200.00"
            ),
            calculation_tax_amount=Decimal(
                "20.00"
            ),
        )


def test_partial_evidence_can_fill_remaining_capacity():
    history = (
        event(
            event_id=1,
            number="PN-A",
            base=Decimal("50"),
            tax=Decimal("10"),
        ),
    )

    validate_new_tax_credit_evidence_against_history(
        target=target(
            number="PN-B",
            base=Decimal("50"),
            tax=Decimal("10"),
        ),
        history=history,
        calculation_taxable_base=Decimal(
            "100"
        ),
        calculation_tax_amount=Decimal(
            "20"
        ),
    )


def test_active_total_cannot_exceed_capacity():
    history = (
        event(
            event_id=1,
            number="PN-A",
            base=Decimal("60"),
            tax=Decimal("12"),
        ),
    )

    with pytest.raises(
        TaxCreditEvidenceCapacityError
    ):
        validate_new_tax_credit_evidence_against_history(
            target=target(
                number="PN-B",
                base=Decimal("50"),
                tax=Decimal("10"),
            ),
            history=history,
            calculation_taxable_base=Decimal(
                "100"
            ),
            calculation_tax_amount=Decimal(
                "20"
            ),
        )


def test_reversed_capacity_can_be_reused_after_reversal_date():
    original = event(
        event_id=1,
        number="PN-A",
        base=Decimal("100"),
        tax=Decimal("20"),
    )

    reversal = event(
        event_id=2,
        number="PN-A",
        effective=D2,
        base=Decimal("100"),
        tax=Decimal("20"),
        reversal_of_id=1,
    )

    validate_new_tax_credit_evidence_against_history(
        target=target(
            number="PN-B",
            available=D2,
        ),
        history=(
            original,
            reversal,
        ),
        calculation_taxable_base=Decimal(
            "100"
        ),
        calculation_tax_amount=Decimal(
            "20"
        ),
    )


def test_capacity_cannot_be_reused_before_future_reversal():
    original = event(
        event_id=1,
        number="PN-A",
    )

    reversal = event(
        event_id=2,
        number="PN-A",
        effective=D3,
        reversal_of_id=1,
    )

    with pytest.raises(
        TaxCreditEvidenceCapacityError
    ):
        validate_new_tax_credit_evidence_against_history(
            target=target(
                number="PN-B",
                available=D2,
            ),
            history=(
                original,
                reversal,
            ),
            calculation_taxable_base=Decimal(
                "100"
            ),
            calculation_tax_amount=Decimal(
                "20"
            ),
        )


def test_same_source_cannot_overlap():
    history = (
        event(
            event_id=1,
            number="PN-1",
        ),
    )

    with pytest.raises(
        TaxCreditEvidenceDuplicateError
    ):
        validate_new_tax_credit_evidence_against_history(
            target=target(
                number="PN-1"
            ),
            history=history,
            calculation_taxable_base=Decimal(
                "100"
            ),
            calculation_tax_amount=Decimal(
                "20"
            ),
        )


def test_same_source_can_be_reintroduced_after_reversal():
    original = event(
        event_id=1,
        number="PN-1",
    )

    reversal = event(
        event_id=2,
        number="PN-1",
        effective=D2,
        reversal_of_id=1,
    )

    validate_new_tax_credit_evidence_against_history(
        target=target(
            number="PN-1",
            available=D2,
        ),
        history=(
            original,
            reversal,
        ),
        calculation_taxable_base=Decimal(
            "100"
        ),
        calculation_tax_amount=Decimal(
            "20"
        ),
    )


def test_reversal_must_copy_original_provenance():
    original = event(
        event_id=1
    )

    reversal = event(
        event_id=2,
        reversal_of_id=1,
    )

    reversal.currency_code = "EUR"

    with pytest.raises(
        TaxCreditEvidenceDataIntegrityError
    ):
        build_tax_credit_evidence_windows(
            events=(
                original,
                reversal,
            )
        )


def test_reversal_cannot_precede_original_effective_date():
    original = event(
        event_id=1,
        effective=D2,
    )

    reversal = event(
        event_id=2,
        effective=D1,
        reversal_of_id=1,
    )

    with pytest.raises(
        TaxCreditEvidenceDataIntegrityError
    ):
        build_tax_credit_evidence_windows(
            events=(
                original,
                reversal,
            )
        )


def test_dangling_reversal_fails_closed():
    reversal = event(
        event_id=2,
        reversal_of_id=999,
    )

    with pytest.raises(
        TaxCreditEvidenceDataIntegrityError
    ):
        build_tax_credit_evidence_windows(
            events=(
                reversal,
            )
        )
