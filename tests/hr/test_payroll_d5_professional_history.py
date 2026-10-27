
"""Positive and negative contracts for dated professional evidence."""

from dataclasses import replace
from datetime import date

import pytest

from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_verified_mapping import D5ProfessionalEvidence
from app.services.payroll_d5_position_provenance import D5PositionEvidence
from app.services.payroll_d5_professional_history import (
    D5ProfessionalHistoryError,
    D5ProfessionalHistoryRecord,
    select_professional_history,
)


def evidence():
    source = D5EventSource(
        company_id=1,
        employment_event_id=42,
        employment_contract_id=12,
        event_type="transfer",
        effective_date=date(2026, 8, 17),
        order_number="17-K",
        order_date=date(2026, 8, 14),
        termination_basis=None,
    )
    professional = D5ProfessionalEvidence(
        employee_id=8,
        tax_number="1234567890",
        professional_name="Documented test profession",
        profession_code="TEST-CODE",
        position_name="Documented test position",
        employment_category="primary",
        evidence_reference="personnel-document-17",
    )
    position = D5PositionEvidence(
        company_id=1,
        employee_id=8,
        employment_contract_id=12,
        position_id=5,
        event_id=42,
        professional=professional,
    )
    record = D5ProfessionalHistoryRecord(
        company_id=1,
        employee_id=8,
        employment_contract_id=12,
        position_id=5,
        effective_from=date(2026, 8, 1),
        effective_to=None,
        documented_on=date(2026, 8, 14),
        evidence_reference=professional.evidence_reference,
        professional_name=professional.professional_name,
        profession_code=professional.profession_code,
        position_name=professional.position_name,
        employment_category=professional.employment_category,
    )
    return source, position, record


def test_single_effective_record():
    source, position, record = evidence()
    assert select_professional_history(
        source=source, position=position, records=(record,)
    ) == record


def test_expired_prior_record_is_not_selected():
    source, position, record = evidence()
    previous = replace(
        record,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 7, 31),
    )
    assert select_professional_history(
        source=source,
        position=position,
        records=(previous, record),
    ) == record


@pytest.mark.parametrize(
    "change",
    [
        {"company_id": 2},
        {"employee_id": 9},
        {"employment_contract_id": 13},
        {"position_id": 6},
        {"documented_on": date(2026, 8, 18)},
        {"effective_from": date(2026, 8, 18)},
        {"professional_name": "Different profession"},
        {"profession_code": "Different code"},
        {"position_name": "Different position"},
        {"evidence_reference": "Different evidence"},
        {"employment_category": "external_secondary"},
    ],
)
def test_invalid_provenance_rejected(change):
    source, position, record = evidence()
    with pytest.raises(D5ProfessionalHistoryError):
        select_professional_history(
            source=source,
            position=position,
            records=(replace(record, **change),),
        )


def test_missing_record_rejected():
    source, position, record = evidence()
    with pytest.raises(D5ProfessionalHistoryError):
        select_professional_history(
            source=source, position=position, records=()
        )


def test_overlapping_professional_history_rejected():
    source, position, record = evidence()
    overlapping = replace(
        record, effective_from=date(2026, 8, 10)
    )
    with pytest.raises(D5ProfessionalHistoryError):
        select_professional_history(
            source=source,
            position=position,
            records=(record, overlapping),
        )


def test_wrong_interval_rejected():
    source, position, record = evidence()
    bad = replace(
        record,
        effective_from=date(2026, 8, 1),
        effective_to=date(2026, 7, 31),
    )
    with pytest.raises(D5ProfessionalHistoryError):
        select_professional_history(
            source=source,
            position=position,
            records=(bad,),
        )
