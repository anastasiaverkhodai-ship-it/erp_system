
"""Pure mapping contracts; not statutory-report acceptance."""
from dataclasses import replace
from datetime import date

import pytest

from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_verified_mapping import (
    D5ProfessionalEvidence,
    D5MappingError,
    map_verified_d5_candidate,
)


def example(event_type="termination"):
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
            } if event_type == "termination" else None
        ),
    )
    professional = D5ProfessionalEvidence(
        employee_id=8,
        tax_number="1234567890",
        professional_name="Продавець непродовольчих товарів",
        profession_code="5220",
        position_name="Продавець",
        employment_category="primary",
        evidence_reference="staffing-schedule-2026",
    )
    return source, professional


@pytest.mark.parametrize(
    "event_type", ["hire", "transfer", "termination"]
)
def test_positive_d5_internal_candidate(event_type):
    source, professional = example(event_type)
    result = map_verified_d5_candidate(source, professional)
    assert result.source_event_id == 42
    assert result.company_id == 1
    assert result.employee_id == 8
    assert result.event_kind == event_type
    assert result.profession_code == "5220"
    if event_type == "termination":
        assert result.termination_provision == "40.1"
        assert result.termination_source_reference == "signed-order-17"
    else:
        assert result.termination_provision is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("tax_number", ""),
        ("professional_name", "  "),
        ("profession_code", ""),
        ("position_name", ""),
        ("employment_category", ""),
        ("evidence_reference", ""),
        ("employee_id", -1),
    ],
)
def test_missing_professional_evidence_rejected(field, value):
    source, professional = example()
    with pytest.raises(D5MappingError):
        map_verified_d5_candidate(
            source, replace(professional, **{field: value})
        )


def test_invalid_termination_ground_rejected():
    source, professional = example()
    source = replace(
        source,
        termination_basis={
            "ground": "redundancy",
            "labour_code_provision": "38",
            "source_reference": "invalid",
        },
    )
    with pytest.raises(D5MappingError):
        map_verified_d5_candidate(source, professional)


def test_event_reversal_not_silently_mapped():
    source, professional = example("termination")
    with pytest.raises(D5MappingError):
        map_verified_d5_candidate(
            replace(source, event_type="reversal"),
            professional,
        )


def test_nontermination_cannot_carry_termination_basis():
    source, professional = example("hire")
    with pytest.raises(D5MappingError):
        map_verified_d5_candidate(
            replace(source, termination_basis={
                "ground": "redundancy",
                "labour_code_provision": "40.1",
                "source_reference": "wrong-event",
            }),
            professional,
        )


def test_order_after_event_is_rejected():
    source, professional = example()
    with pytest.raises(D5MappingError):
        map_verified_d5_candidate(
            replace(source, order_date=date(2026, 8, 18)),
            professional,
        )


def test_invalid_scope_is_rejected():
    source, professional = example()
    with pytest.raises(D5MappingError):
        map_verified_d5_candidate(
            replace(source, company_id=0),
            professional,
        )
