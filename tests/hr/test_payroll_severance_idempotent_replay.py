from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as N

import pytest

from app.services.payroll_severance_verified_source_service import (
    VerifiedSeveranceSourceError,
    verify_exact_initial_replay,
)


def evidence():
    basis = N(
        company_id=1,
        employment_contract_id=2,
        termination_event_id=3,
        termination_date=date(2026, 9, 30),
        order_number="ORDER-1",
        basis_as_of=date(2026, 9, 30),
        basis_reference="Verified July-August payroll",
        base_amount=D("12000.00"),
        gross_amount=D("12000.00"),
    )
    policy = N(category="average_months", multiplier=1)
    legal = N(
        ground="redundancy",
        labour_code_provision="40.1",
        source_reference="Order and legal basis",
    )
    row = N(
        company_id=1,
        employment_contract_id=2,
        payroll_input_id=4,
        termination_event_id=3,
        revision=1,
        supersedes_id=None,
        termination_date=basis.termination_date,
        order_number=basis.order_number,
        policy_ground=legal.ground,
        policy_category=policy.category,
        labour_code_provision=legal.labour_code_provision,
        source_reference=legal.source_reference,
        multiplier=policy.multiplier,
        basis_as_of=basis.basis_as_of,
        basis_reference=basis.basis_reference,
        base_amount=basis.base_amount,
        gross_amount=basis.gross_amount,
        currency_code="UAH",
        created_by=5,
    )
    kwargs = dict(
        basis=basis,
        policy=policy,
        termination_basis=legal,
        payroll_input_id=4,
        created_by=5,
    )
    return row, kwargs


def test_identical_replay_returns_original():
    row, kwargs = evidence()
    assert verify_exact_initial_replay(row, **kwargs) is row


@pytest.mark.parametrize("field,value", [
    ("company_id", 99),
    ("employment_contract_id", 99),
    ("payroll_input_id", 99),
    ("termination_event_id", 99),
    ("revision", 2),
    ("supersedes_id", 12),
    ("termination_date", date(2026, 9, 29)),
    ("order_number", "CHANGED"),
    ("policy_ground", "other"),
    ("policy_category", "minimum_wages"),
    ("labour_code_provision", "OTHER"),
    ("source_reference", "CHANGED"),
    ("multiplier", 6),
    ("basis_as_of", date(2026, 9, 29)),
    ("basis_reference", "CHANGED"),
    ("base_amount", D("12001.00")),
    ("gross_amount", D("12001.00")),
    ("currency_code", "USD"),
    ("created_by", 77),
])
def test_changed_evidence_rejected(field, value):
    row, kwargs = evidence()
    setattr(row, field, value)

    with pytest.raises(
        VerifiedSeveranceSourceError,
        match="does not match verified evidence",
    ):
        verify_exact_initial_replay(row, **kwargs)
