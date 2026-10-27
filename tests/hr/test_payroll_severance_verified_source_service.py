from types import SimpleNamespace as N
from datetime import date
from decimal import Decimal as D

import pytest

from app.services.payroll_severance_verified_source_service import (
    VerifiedSeveranceSourceError,
    require_request_key,
    require_initial_source_absent,
    validate_source_identity,
)


@pytest.mark.parametrize("value", [
    "", "   ", "x" * 201, None, 12, True
])
def test_invalid_request_keys(value):
    with pytest.raises(VerifiedSeveranceSourceError):
        require_request_key(value)


def test_valid_request_key():
    assert require_request_key("  SR-001  ") == "SR-001"


def test_first_revision_is_available():
    require_initial_source_absent([], "SR-001")


def test_existing_same_request_requires_verification():
    with pytest.raises(VerifiedSeveranceSourceError):
        require_initial_source_absent(
            [N(request_key="SR-001")],
            "SR-001",
        )


def test_existing_other_revision_requires_correction():
    with pytest.raises(VerifiedSeveranceSourceError):
        require_initial_source_absent(
            [N(request_key="SR-OLD")],
            "SR-NEW",
        )


def sample():
    return dict(
        basis=N(
            company_id=1,
            employment_contract_id=10,
            termination_event_id=25,
            termination_date=date(2026, 9, 30),
            order_number="TERM-001",
            policy_category="average_months",
            multiplier=1,
            base_amount=D("12000"),
            gross_amount=D("12000"),
        ),
        payroll_input=N(
            company_id=1,
            employment_contract_id=10,
        ),
        event=N(
            id=25,
            company_id=1,
            employment_contract_id=10,
            effective_date=date(2026, 9, 30),
            order_number="TERM-001",
            event_type="termination",
        ),
        termination_basis=N(
            ground="redundancy",
            labour_code_provision="40.1",
            source_reference="Verified order",
        ),
        company_id=1,
        employment_contract_id=10,
    )


def test_matching_legal_identity():
    validate_source_identity(**sample())


@pytest.mark.parametrize("target, field, value", [
    ("basis", "company_id", 2),
    ("basis", "employment_contract_id", 11),
    ("basis", "termination_event_id", 26),
    ("basis", "termination_date", date(2026, 10, 1)),
    ("basis", "order_number", "DIFFERENT"),
    ("payroll_input", "company_id", 2),
    ("payroll_input", "employment_contract_id", 11),
    ("event", "company_id", 2),
    ("event", "employment_contract_id", 11),
    ("event", "event_type", "hire"),
    ("termination_basis", "source_reference", ""),
    ("termination_basis", "ground", ""),
])
def test_identity_mismatch_rejected(target, field, value):
    data = sample()
    setattr(data[target], field, value)
    with pytest.raises(VerifiedSeveranceSourceError):
        validate_source_identity(**data)
