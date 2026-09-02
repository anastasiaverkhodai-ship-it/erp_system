from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.input_tax_recognition_persistence_service import (
    InputTaxRecognitionPersistenceCapacityError,
    InputTaxRecognitionPersistenceIntegrityError,
    InputTaxRecognitionPersistenceStateError,
    InputTaxRecognitionPersistenceTarget,
    build_input_tax_recognition_source_plan,
)
from app.services.tax_credit_evidence_types import (
    TaxCreditEvidenceType,
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


def evidence(
    *,
    event_id,
    number,
    effective_date=D1,
    base="100.00",
    tax="20.00",
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


def recognition(
    *,
    event_id,
    evidence_id,
    recognition_date=D1,
    base="100.00",
    tax="20.00",
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        tax_calculation_id=10,
        invoice_fulfillment_allocation_id=None,
        payment_settlement_allocation_id=None,
        tax_credit_evidence_id=evidence_id,
        recognition_date=recognition_date,
        recognized_taxable_base=Decimal(
            base
        ),
        recognized_tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def target(
    *,
    evidence_id=1,
    recognition_date=D1,
    base="100.00",
    tax="20.00",
):
    return InputTaxRecognitionPersistenceTarget(
        tax_credit_evidence_id=(
            evidence_id
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


def test_new_positive_target_creates_full_replacement():
    desired = target(
        base="50.00",
        tax="10.00",
    )

    plan = build_input_tax_recognition_source_plan(
        calculation=calculation(),
        evidence_events=(
            evidence(
                event_id=1,
                number="PN-1",
            ),
        ),
        recognition_events=(),
        target=desired,
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target == desired


def test_exact_current_target_is_noop():
    plan = build_input_tax_recognition_source_plan(
        calculation=calculation(),
        evidence_events=(
            evidence(
                event_id=1,
                number="PN-1",
            ),
        ),
        recognition_events=(
            recognition(
                event_id=10,
                evidence_id=1,
            ),
        ),
        target=target(),
    )

    assert plan.is_noop


def test_changed_positive_same_tranche_reverses_and_replaces():
    desired = target(
        recognition_date=D1,
        base="50.00",
        tax="10.00",
    )

    plan = build_input_tax_recognition_source_plan(
        calculation=calculation(),
        evidence_events=(
            evidence(
                event_id=1,
                number="PN-1",
            ),
        ),
        recognition_events=(
            recognition(
                event_id=10,
                evidence_id=1,
            ),
        ),
        target=desired,
    )

    assert (
        plan.reversal_event_ids
        == (10,)
    )
    assert plan.replacement_target == desired


def test_zero_target_reverses_active_original():
    desired = target(
        recognition_date=D1,
        base="0",
        tax="0",
    )

    plan = build_input_tax_recognition_source_plan(
        calculation=calculation(),
        evidence_events=(
            evidence(
                event_id=1,
                number="PN-1",
            ),
        ),
        recognition_events=(
            recognition(
                event_id=10,
                evidence_id=1,
            ),
        ),
        target=desired,
    )

    assert (
        plan.reversal_event_ids
        == (10,)
    )
    assert plan.replacement_target is None


def test_zero_target_without_current_is_noop():
    plan = build_input_tax_recognition_source_plan(
        calculation=calculation(),
        evidence_events=(
            evidence(
                event_id=1,
                number="PN-1",
            ),
        ),
        recognition_events=(),
        target=target(
            recognition_date=D2,
            base="0",
            tax="0",
        ),
    )

    assert plan.is_noop


def test_target_cannot_exceed_evidence_capacity():
    with pytest.raises(
        InputTaxRecognitionPersistenceCapacityError
    ):
        build_input_tax_recognition_source_plan(
            calculation=calculation(),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                    base="50.00",
                    tax="10.00",
                ),
            ),
            recognition_events=(),
            target=target(
                base="50.01",
                tax="10.01",
            ),
        )


def test_target_cannot_use_evidence_before_available_date():
    with pytest.raises(
        InputTaxRecognitionPersistenceStateError
    ):
        build_input_tax_recognition_source_plan(
            calculation=calculation(),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                    effective_date=D2,
                ),
            ),
            recognition_events=(),
            target=target(
                recognition_date=D1,
            ),
        )


def test_positive_target_cannot_use_reversed_evidence():
    original = evidence(
        event_id=1,
        number="PN-1",
        effective_date=D1,
    )

    reversal = evidence(
        event_id=2,
        number="PN-1",
        effective_date=D2,
        reversal_of_id=1,
    )

    with pytest.raises(
        InputTaxRecognitionPersistenceStateError
    ):
        build_input_tax_recognition_source_plan(
            calculation=calculation(),
            evidence_events=(
                original,
                reversal,
            ),
            recognition_events=(),
            target=target(
                recognition_date=D2,
            ),
        )


def test_zero_target_can_reconcile_reversed_evidence():
    original = evidence(
        event_id=1,
        number="PN-1",
    )

    reversal = evidence(
        event_id=2,
        number="PN-1",
        effective_date=D2,
        reversal_of_id=1,
    )

    plan = build_input_tax_recognition_source_plan(
        calculation=calculation(),
        evidence_events=(
            original,
            reversal,
        ),
        recognition_events=(
            recognition(
                event_id=10,
                evidence_id=1,
            ),
        ),
        target=target(
            recognition_date=D1,
            base="0",
            tax="0",
        ),
    )

    assert (
        plan.reversal_event_ids
        == (10,)
    )


def test_total_after_source_change_cannot_exceed_calculation():
    with pytest.raises(
        InputTaxRecognitionPersistenceCapacityError
    ):
        build_input_tax_recognition_source_plan(
            calculation=calculation(),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-A",
                    base="60.00",
                    tax="12.00",
                ),
                evidence(
                    event_id=2,
                    number="PN-B",
                    base="60.00",
                    tax="12.00",
                ),
            ),
            recognition_events=(
                recognition(
                    event_id=10,
                    evidence_id=1,
                    base="60.00",
                    tax="12.00",
                ),
            ),
            target=target(
                evidence_id=2,
                base="60.00",
                tax="12.00",
            ),
        )


def test_multiple_active_originals_for_same_source_fail_closed():
    with pytest.raises(
        InputTaxRecognitionPersistenceIntegrityError
    ):
        build_input_tax_recognition_source_plan(
            calculation=calculation(),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                ),
            ),
            recognition_events=(
                recognition(
                    event_id=10,
                    evidence_id=1,
                    base="50",
                    tax="10",
                ),
                recognition(
                    event_id=11,
                    evidence_id=1,
                    base="50",
                    tax="10",
                ),
            ),
            target=target(),
        )


def test_input_active_event_with_output_source_fails_closed():
    bad = recognition(
        event_id=10,
        evidence_id=1,
    )

    bad.invoice_fulfillment_allocation_id = 99

    with pytest.raises(
        InputTaxRecognitionPersistenceIntegrityError
    ):
        build_input_tax_recognition_source_plan(
            calculation=calculation(),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                ),
            ),
            recognition_events=(
                bad,
            ),
            target=target(),
        )


def test_recognition_reversal_must_copy_source_and_amounts():
    original = recognition(
        event_id=10,
        evidence_id=1,
    )

    reversal = recognition(
        event_id=11,
        evidence_id=1,
        recognition_date=D2,
        tax="19.00",
        reversal_of_id=10,
    )

    with pytest.raises(
        InputTaxRecognitionPersistenceIntegrityError
    ):
        build_input_tax_recognition_source_plan(
            calculation=calculation(),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                ),
            ),
            recognition_events=(
                original,
                reversal,
            ),
            target=target(),
        )


def test_same_evidence_can_have_two_active_date_tranches():
    plan = build_input_tax_recognition_source_plan(
        calculation=calculation(),
        evidence_events=(
            evidence(
                event_id=1,
                number="PN-1",
            ),
        ),
        recognition_events=(
            recognition(
                event_id=10,
                evidence_id=1,
                recognition_date=D2,
                base="50.00",
                tax="10.00",
            ),
        ),
        target=target(
            recognition_date=D1,
            base="50.00",
            tax="10.00",
        ),
    )

    assert plan.reversal_event_ids == ()

    assert (
        plan.replacement_target
        == target(
            recognition_date=D1,
            base="50.00",
            tax="10.00",
        )
    )


def test_same_evidence_tranches_cannot_exceed_evidence_capacity():
    with pytest.raises(
        InputTaxRecognitionPersistenceCapacityError
    ):
        build_input_tax_recognition_source_plan(
            calculation=calculation(),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                    base="100.00",
                    tax="20.00",
                ),
            ),
            recognition_events=(
                recognition(
                    event_id=10,
                    evidence_id=1,
                    recognition_date=D1,
                    base="60.00",
                    tax="12.00",
                ),
            ),
            target=target(
                recognition_date=D2,
                base="50.00",
                tax="10.00",
            ),
        )


def test_output_calculation_rejected():
    with pytest.raises(
        InputTaxRecognitionPersistenceStateError
    ):
        build_input_tax_recognition_source_plan(
            calculation=calculation(
                direction=TaxDirection.OUTPUT
            ),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                ),
            ),
            recognition_events=(),
            target=target(),
        )


def test_manual_input_calculation_rejected():
    with pytest.raises(
        InputTaxRecognitionPersistenceStateError
    ):
        build_input_tax_recognition_source_plan(
            calculation=calculation(
                method=TaxRecognitionMethod.MANUAL
            ),
            evidence_events=(
                evidence(
                    event_id=1,
                    number="PN-1",
                ),
            ),
            recognition_events=(),
            target=target(),
        )
