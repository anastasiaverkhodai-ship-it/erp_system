"""Fail-closed additional payroll source consistency tests."""

from copy import deepcopy
from decimal import Decimal

import pytest

from app.services.payroll_correction_additional_source_proof import (
    PayrollAdditionalSourceProofError,
    compare_additional_source_lines,
)


@pytest.mark.parametrize(
    "kind,reference",
    [
        ("supplement", "source_supplement_id"),
        ("vacation_pay", "source_vacation_calculation_id"),
        ("sick_pay", "source_sick_leave_calculation_id"),
    ],
)
def test_matching_source_passes_but_does_not_authorize(kind, reference):
    row = {
        "line_type": kind,
        reference: 12,
        "amount": Decimal("250.00"),
        "quantity": Decimal("2.00"),
        "rate": Decimal("125.00"),
        "currency_code": "UAH",
    }

    result = compare_additional_source_lines(
        kind=kind,
        stored_lines=[row],
        derived_lines=[deepcopy(row)],
    )

    assert result["source_values_reconciled"] is True
    assert result["historical_source_immutability_verified"] is False
    assert result["automatic_revision_creation_allowed"] is False
    assert result["financial_execution_allowed"] is False


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("amount", Decimal("300.00")),
        ("quantity", Decimal("3.00")),
        ("rate", Decimal("130.00")),
        ("currency_code", "EUR"),
        ("source_supplement_id", 99),
    ],
)
def test_supplement_mutation_rejected(field, new_value):
    original = {
        "line_type": "supplement",
        "source_supplement_id": 12,
        "amount": Decimal("250.00"),
        "quantity": Decimal("2.00"),
        "rate": Decimal("125.00"),
        "currency_code": "UAH",
    }

    changed = deepcopy(original)
    changed[field] = new_value

    with pytest.raises(
        PayrollAdditionalSourceProofError,
        match="provenance mismatch",
    ):
        compare_additional_source_lines(
            kind="supplement",
            stored_lines=[original],
            derived_lines=[changed],
        )


def test_missing_reference_rejected():
    row = {
        "line_type": "vacation_pay",
        "source_vacation_calculation_id": None,
        "amount": Decimal("100.00"),
        "quantity": Decimal("1"),
        "rate": Decimal("100"),
        "currency_code": "UAH",
    }

    with pytest.raises(
        PayrollAdditionalSourceProofError,
        match="source_vacation_calculation_id",
    ):
        compare_additional_source_lines(
            kind="vacation_pay",
            stored_lines=[row],
            derived_lines=[row],
        )


def test_nonfinite_amount_rejected():
    row = {
        "line_type": "sick_pay",
        "source_sick_leave_calculation_id": 1,
        "amount": Decimal("NaN"),
        "quantity": Decimal("1"),
        "rate": Decimal("1"),
        "currency_code": "UAH",
    }

    with pytest.raises(
        PayrollAdditionalSourceProofError,
        match="Non-finite",
    ):
        compare_additional_source_lines(
            kind="sick_pay",
            stored_lines=[row],
            derived_lines=[row],
        )


def test_empty_components_do_not_grant_authorization():
    result = compare_additional_source_lines(
        kind="supplement",
        stored_lines=[],
        derived_lines=[],
    )

    assert result["source_values_reconciled"] is True
    assert result["automatic_revision_creation_allowed"] is False


def test_added_component_is_rejected():
    row = {
        "line_type": "supplement",
        "source_supplement_id": 8,
        "amount": Decimal("10.00"),
        "quantity": Decimal("1"),
        "rate": Decimal("10"),
        "currency_code": "UAH",
    }

    with pytest.raises(
        PayrollAdditionalSourceProofError,
        match="provenance mismatch",
    ):
        compare_additional_source_lines(
            kind="supplement",
            stored_lines=[],
            derived_lines=[row],
        )


def test_duplicate_component_is_rejected():
    row = {
        "line_type": "supplement",
        "source_supplement_id": 8,
        "amount": Decimal("10.00"),
        "quantity": Decimal("1"),
        "rate": Decimal("10"),
        "currency_code": "UAH",
    }

    with pytest.raises(
        PayrollAdditionalSourceProofError,
        match="provenance mismatch",
    ):
        compare_additional_source_lines(
            kind="supplement",
            stored_lines=[row],
            derived_lines=[row, row],
        )
