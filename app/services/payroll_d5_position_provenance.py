
"""Verify the historical ERP position underlying a D5 candidate.

This is an internal provenance guard, not an official classifier mapping.
Position.code must never be assumed to be a national profession code.
"""

from dataclasses import dataclass
from typing import Mapping

from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_verified_mapping import (
    D5ProfessionalEvidence,
    D5MappingError,
    D5MappedCandidate,
    map_verified_d5_candidate,
)


class D5PositionProvenanceError(D5MappingError):
    pass


@dataclass(frozen=True)
class D5PositionEvidence:
    company_id: int
    employee_id: int
    employment_contract_id: int
    position_id: int
    event_id: int
    professional: D5ProfessionalEvidence


def _positive(value, label):
    if type(value) is not int or value <= 0:
        raise D5PositionProvenanceError(
            f"Valid {label} required"
        )
    return value


def verify_d5_position_provenance(
    *,
    source: D5EventSource,
    position: D5PositionEvidence,
    before_state: Mapping,
    after_state: Mapping,
) -> D5MappedCandidate:
    """Reject mismatched identities or undocumented historical positions."""

    if not isinstance(source, D5EventSource):
        raise D5PositionProvenanceError("Typed event required")
    if not isinstance(position, D5PositionEvidence):
        raise D5PositionProvenanceError("Typed position evidence required")
    if not isinstance(position.professional, D5ProfessionalEvidence):
        raise D5PositionProvenanceError("Typed professional evidence required")

    for value, label in (
        (source.company_id, "source company"),
        (source.employment_event_id, "source event"),
        (source.employment_contract_id, "source contract"),
        (position.company_id, "position company"),
        (position.employee_id, "position employee"),
        (position.employment_contract_id, "position contract"),
        (position.position_id, "position"),
        (position.event_id, "position event"),
    ):
        _positive(value, label)

    if (
        position.company_id != source.company_id
        or position.event_id != source.employment_event_id
        or position.employment_contract_id != source.employment_contract_id
        or position.employee_id != position.professional.employee_id
    ):
        raise D5PositionProvenanceError(
            "Company, event, contract or employee mismatch"
        )

    if source.event_type not in ("hire", "transfer", "termination"):
        raise D5PositionProvenanceError(
            "Unsupported professional event"
        )

    if not isinstance(before_state, Mapping) or not isinstance(
        after_state, Mapping
    ):
        raise D5PositionProvenanceError(
            "Structured immutable event states required"
        )

    # Employment state after hire/transfer; immediately prior to termination.
    state = (
        before_state
        if source.event_type == "termination"
        else after_state
    )

    historic_position = state.get("position_id")

    if type(historic_position) is not int or historic_position <= 0:
        raise D5PositionProvenanceError(
            "Documented historical position ID required"
        )

    if historic_position != position.position_id:
        raise D5PositionProvenanceError(
            "Professional position differs from historical event"
        )

    # The caller must have independently verified that the professional
    # evidence is authentic for this position on the effective event date.
    # This method only verifies identity and event-state consistency.
    return map_verified_d5_candidate(
        source,
        position.professional,
    )
