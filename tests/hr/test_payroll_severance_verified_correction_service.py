from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as N

import pytest

from app.services.payroll_severance_verified_correction_service import (
    CorrectionEvidence,
    require_correction_evidence,
    validate_new_revision,
)
from app.services.payroll_severance_verified_source_service import (
    VerifiedSeveranceSourceError,
)


def sample():
    previous = N(
        company_id=1,
        employment_contract_id=10,
        payroll_input_id=50,
        termination_event_id=25,
        termination_date=date(2026, 9, 30),
        order_number="TERM-001",
        policy_category="average_months",
        multiplier=1,
        base_amount=D("12000.00"),
        gross_amount=D("12000.00"),
        basis_reference="Original verified payroll",
    )
    basis = N(
        company_id=1,
        employment_contract_id=10,
        termination_event_id=25,
        termination_date=date(2026, 9, 30),
        order_number="TERM-001",
        policy_category="average_months",
        multiplier=1,
        base_amount=D("12500.00"),
        gross_amount=D("12500.00"),
        basis_reference="Corrected verified payroll",
    )
    evidence = CorrectionEvidence(
        reference="CORRECTION-ORDER-2026-10",
        effective_date=date(2026, 10, 15),
    )
    return previous, basis, evidence


def validate(previous, basis, evidence):
    validate_new_revision(
        previous,
        basis,
        evidence,
        company_id=1,
        employment_contract_id=10,
        payroll_input_id=50,
    )


def test_documented_changed_average_accepted():
    validate(*sample())


@pytest.mark.parametrize("bad_reference", [
    "", " ", "abc", "x" * 1001
])
def test_bad_evidence_reference_blocked(bad_reference):
    previous, basis, _ = sample()
    with pytest.raises(VerifiedSeveranceSourceError):
        validate(
            previous,
            basis,
            CorrectionEvidence(
                reference=bad_reference,
                effective_date=date(2026, 10, 15),
            ),
        )


def test_no_change_blocked():
    previous, basis, evidence = sample()
    basis.base_amount = previous.base_amount
    basis.gross_amount = previous.gross_amount
    basis.basis_reference = previous.basis_reference
    with pytest.raises(VerifiedSeveranceSourceError):
        validate(previous, basis, evidence)


@pytest.mark.parametrize("field,value", [
    ("company_id", 2),
    ("employment_contract_id", 11),
    ("termination_event_id", 99),
    ("termination_date", date(2026, 10, 1)),
    ("order_number", "OTHER"),
    ("policy_category", "minimum_wages"),
    ("multiplier", 6),
])
def test_scope_or_identity_change_blocked(field, value):
    previous, basis, evidence = sample()
    setattr(basis, field, value)
    with pytest.raises(VerifiedSeveranceSourceError):
        validate(previous, basis, evidence)


@pytest.mark.parametrize("amount", [
    D("0"), D("-1"), D("NaN"), D("Infinity")
])
def test_invalid_corrected_base_blocked(amount):
    previous, basis, evidence = sample()
    basis.base_amount = amount
    with pytest.raises(VerifiedSeveranceSourceError):
        validate(previous, basis, evidence)


def test_correction_before_termination_blocked():
    previous, basis, _ = sample()
    with pytest.raises(VerifiedSeveranceSourceError):
        validate(
            previous,
            basis,
            CorrectionEvidence(
                reference="CORRECTION-ORDER-2026",
                effective_date=date(2026, 9, 1),
            ),
        )


def test_reference_only_change_blocked():
    previous, basis, evidence = sample()
    basis.base_amount = previous.base_amount
    basis.gross_amount = previous.gross_amount
    basis.basis_reference = "Reference changed only"

    with pytest.raises(
        VerifiedSeveranceSourceError,
        match="No changed verified source evidence",
    ):
        validate(previous, basis, evidence)


def test_amount_change_without_source_change_blocked():
    previous, basis, evidence = sample()
    basis.basis_reference = previous.basis_reference

    with pytest.raises(
        VerifiedSeveranceSourceError,
        match="changed verified source reference",
    ):
        validate(previous, basis, evidence)


def test_amount_and_source_change_accepted():
    previous, basis, evidence = sample()
    assert basis.gross_amount != previous.gross_amount
    assert basis.basis_reference != previous.basis_reference
    validate(previous, basis, evidence)
