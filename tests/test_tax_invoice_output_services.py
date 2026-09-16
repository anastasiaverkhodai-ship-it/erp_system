from decimal import Decimal

import pytest

from app.services.tax_invoice_output_persistence_service import (
    TaxInvoiceOutputSnapshotError,
    build_output_tax_invoice_line_amounts,
)
from app.services.tax_invoice_registration_lifecycle_service import (
    TaxInvoiceRegistrationTransitionError,
    validate_tax_invoice_registration_transition,
)


def test_full_recognition_keeps_full_quantity():
    result = build_output_tax_invoice_line_amounts(
        source_line_quantity=Decimal("10"),
        calculation_taxable_base=Decimal("100.00"),
        recognized_taxable_base=Decimal("100.00"),
        recognized_tax_amount=Decimal("20.00"),
    )

    assert result.quantity == Decimal("10.000000")
    assert (
        result.unit_price_without_vat
        == Decimal("10.000000")
    )
    assert result.taxable_base == Decimal("100.00")
    assert result.tax_amount == Decimal("20.00")
    assert result.total_with_vat == Decimal("120.00")


def test_partial_first_event_builds_proportional_quantity():
    result = build_output_tax_invoice_line_amounts(
        source_line_quantity=Decimal("10"),
        calculation_taxable_base=Decimal("100.00"),
        recognized_taxable_base=Decimal("30.00"),
        recognized_tax_amount=Decimal("6.00"),
    )

    assert result.quantity == Decimal("3.000000")
    assert (
        result.unit_price_without_vat
        == Decimal("10.000000")
    )
    assert result.total_with_vat == Decimal("36.00")


def test_recognition_cannot_exceed_calculation_base():
    with pytest.raises(
        TaxInvoiceOutputSnapshotError,
        match="exceeds",
    ):
        build_output_tax_invoice_line_amounts(
            source_line_quantity=Decimal("10"),
            calculation_taxable_base=Decimal("100.00"),
            recognized_taxable_base=Decimal("100.01"),
            recognized_tax_amount=Decimal("20.00"),
        )


@pytest.mark.parametrize(
    "calculation_base,recognized_base",
    [
        ("0.00", "0.00"),
        ("0.00", "1.00"),
        ("100.00", "0.00"),
    ],
)
def test_v1_fails_closed_for_zero_base_cases(
    calculation_base,
    recognized_base,
):
    with pytest.raises(
        TaxInvoiceOutputSnapshotError
    ):
        build_output_tax_invoice_line_amounts(
            source_line_quantity=Decimal("10"),
            calculation_taxable_base=Decimal(
                calculation_base
            ),
            recognized_taxable_base=Decimal(
                recognized_base
            ),
            recognized_tax_amount=Decimal("0.00"),
        )


def test_registration_must_start_prepared():
    validate_tax_invoice_registration_transition(
        previous_status=None,
        new_status="prepared",
    )

    with pytest.raises(
        TaxInvoiceRegistrationTransitionError,
        match="first OUTPUT",
    ):
        validate_tax_invoice_registration_transition(
            previous_status=None,
            new_status="registered",
        )


@pytest.mark.parametrize(
    "previous,new",
    [
        ("prepared", "submitted"),
        ("prepared", "registered"),
        ("prepared", "suspended"),
        ("prepared", "rejected"),
        ("submitted", "registered"),
        ("submitted", "suspended"),
        ("submitted", "rejected"),
        ("suspended", "submitted"),
        ("suspended", "registered"),
        ("suspended", "rejected"),
    ],
)
def test_allowed_registration_transitions(
    previous,
    new,
):
    validate_tax_invoice_registration_transition(
        previous_status=previous,
        new_status=new,
    )


@pytest.mark.parametrize(
    "terminal",
    ["registered", "rejected"],
)
def test_terminal_registration_states_are_immutable(
    terminal,
):
    with pytest.raises(
        TaxInvoiceRegistrationTransitionError,
        match="not allowed",
    ):
        validate_tax_invoice_registration_transition(
            previous_status=terminal,
            new_status="submitted",
        )
