
"""Fail-closed historical position contract tests."""

from dataclasses import replace
from datetime import date

import pytest

from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_verified_mapping import D5ProfessionalEvidence
from app.services.payroll_d5_position_provenance import (
    D5PositionEvidence,
    D5PositionProvenanceError,
    verify_d5_position_provenance,
)


def fixture(event_type="termination"):
    source = D5EventSource(
        company_id=1,
        employment_event_id=42,
        employment_contract_id=12,
        event_type=event_type,
        effective_date=date(2026, 8, 17),
        order_number="17-K",
        order_date=date(2026, 8, 14),
        termination_basis=(
            {
                "ground": "redundancy",
                "labour_code_provision": "40.1",
                "source_reference": "signed-order-17",
            }
            if event_type == "termination" else None
        ),
    )
    professional = D5ProfessionalEvidence(
        employee_id=8,
        tax_number="1234567890",
        professional_name="Verified example profession",
        profession_code="EXAMPLE-CODE",
        position_name="Verified example position",
        employment_category="primary",
        evidence_reference="verified-personnel-evidence",
    )
    position = D5PositionEvidence(
        company_id=1,
        employee_id=8,
        employment_contract_id=12,
        position_id=5,
        event_id=42,
        professional=professional,
    )
    return source, position


@pytest.mark.parametrize("event_type", ["hire", "transfer", "termination"])
def test_matching_event_state_passes(event_type):
    source, position = fixture(event_type)
    result = verify_d5_position_provenance(
        source=source,
        position=position,
        before_state={"position_id": 5},
        after_state={"position_id": 5},
    )
    assert result.source_event_id == 42
    assert result.employee_id == 8


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id", 2),
        ("employee_id", 99),
        ("employment_contract_id", 99),
        ("position_id", 99),
        ("event_id", 99),
    ],
)
def test_mismatched_evidence_fails(field, value):
    source, position = fixture()
    with pytest.raises(D5PositionProvenanceError):
        verify_d5_position_provenance(
            source=source,
            position=replace(position, **{field: value}),
            before_state={"position_id": 5},
            after_state={"position_id": 5},
        )


def test_termination_uses_pretermination_state():
    source, position = fixture()
    result = verify_d5_position_provenance(
        source=source,
        position=position,
        before_state={"position_id": 5},
        after_state={"position_id": None},
    )
    assert result.event_kind == "termination"


def test_transfer_uses_new_position():
    source, position = fixture("transfer")
    result = verify_d5_position_provenance(
        source=source,
        position=position,
        before_state={"position_id": 7},
        after_state={"position_id": 5},
    )
    assert result.event_kind == "transfer"


@pytest.mark.parametrize(
    "state",
    [{}, {"position_id": None}, {"position_id": "5"}],
)
def test_missing_or_untyped_history_rejected(state):
    source, position = fixture()
    with pytest.raises(D5PositionProvenanceError):
        verify_d5_position_provenance(
            source=source,
            position=position,
            before_state=state,
            after_state={"position_id": 5},
        )


def test_different_historical_position_rejected():
    source, position = fixture()
    with pytest.raises(D5PositionProvenanceError):
        verify_d5_position_provenance(
            source=source,
            position=position,
            before_state={"position_id": 7},
            after_state={"position_id": 5},
        )


def test_professional_employee_mismatch_rejected():
    source, position = fixture()
    position = replace(
        position,
        professional=replace(position.professional, employee_id=99),
    )
    with pytest.raises(D5PositionProvenanceError):
        verify_d5_position_provenance(
            source=source,
            position=position,
            before_state={"position_id": 5},
            after_state={"position_id": 5},
        )
