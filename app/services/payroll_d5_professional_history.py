
"""Temporal provenance contract for D5 professional evidence.

The caller must obtain the evidence from a verified persisted source.
This module does not authenticate documents or certify profession codes.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_position_provenance import (
    D5PositionEvidence,
    D5PositionProvenanceError,
)


class D5ProfessionalHistoryError(D5PositionProvenanceError):
    pass


@dataclass(frozen=True)
class D5ProfessionalHistoryRecord:
    company_id: int
    employee_id: int
    employment_contract_id: int
    position_id: int
    effective_from: date
    effective_to: date | None
    documented_on: date
    evidence_reference: str
    professional_name: str
    profession_code: str
    position_name: str
    employment_category: Literal[
        "primary", "internal_secondary", "external_secondary"
    ]


def _id(value, name):
    if type(value) is not int or value <= 0:
        raise D5ProfessionalHistoryError(f"Invalid {name}")


def _date(value, name):
    if type(value) is not date:
        raise D5ProfessionalHistoryError(f"Invalid {name}")


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise D5ProfessionalHistoryError(f"Missing {name}")
    return value.strip()


def validate_professional_history(
    *,
    source: D5EventSource,
    position: D5PositionEvidence,
    record: D5ProfessionalHistoryRecord,
) -> None:
    """Validate one documented historical record for a dated event."""

    if not isinstance(source, D5EventSource):
        raise D5ProfessionalHistoryError("Typed HR source required")
    if not isinstance(position, D5PositionEvidence):
        raise D5ProfessionalHistoryError("Typed position evidence required")
    if not isinstance(record, D5ProfessionalHistoryRecord):
        raise D5ProfessionalHistoryError("Typed history required")

    for name, value in (
        ("company", record.company_id),
        ("employee", record.employee_id),
        ("contract", record.employment_contract_id),
        ("position", record.position_id),
        ("source company", source.company_id),
        ("source contract", source.employment_contract_id),
        ("position event", position.event_id),
        ("source event", source.employment_event_id),
    ):
        _id(value, name)

    if (
        record.company_id != source.company_id
        or record.company_id != position.company_id
        or record.employee_id != position.employee_id
        or record.employee_id != position.professional.employee_id
        or record.employment_contract_id != source.employment_contract_id
        or record.employment_contract_id != position.employment_contract_id
        or record.position_id != position.position_id
        or source.employment_event_id != position.event_id
    ):
        raise D5ProfessionalHistoryError(
            "Historical professional source identity mismatch"
        )

    _date(source.effective_date, "event date")
    _date(record.effective_from, "validity start")
    _date(record.documented_on, "documentation date")

    if record.effective_to is not None:
        _date(record.effective_to, "validity end")
        if record.effective_to < record.effective_from:
            raise D5ProfessionalHistoryError(
                "Invalid professional history interval"
            )

    if source.effective_date < record.effective_from:
        raise D5ProfessionalHistoryError(
            "Professional history starts after event"
        )

    if (
        record.effective_to is not None
        and source.effective_date > record.effective_to
    ):
        raise D5ProfessionalHistoryError(
            "Professional history expired before event"
        )

    if record.documented_on > source.effective_date:
        raise D5ProfessionalHistoryError(
            "Later evidence requires a documented correction workflow"
        )

    fields = (
        (record.evidence_reference, "document reference"),
        (record.professional_name, "professional name"),
        (record.profession_code, "profession code"),
        (record.position_name, "position name"),
    )
    for value, name in fields:
        _text(value, name)

    if record.employment_category not in (
        "primary",
        "internal_secondary",
        "external_secondary",
    ):
        raise D5ProfessionalHistoryError(
            "Unsupported employment category"
        )

    claimed = position.professional

    if (
        claimed.evidence_reference != record.evidence_reference
        or claimed.professional_name != record.professional_name
        or claimed.profession_code != record.profession_code
        or claimed.position_name != record.position_name
        or claimed.employment_category != record.employment_category
    ):
        raise D5ProfessionalHistoryError(
            "Professional data differs from historical record"
        )


def select_professional_history(
    *,
    source: D5EventSource,
    position: D5PositionEvidence,
    records: tuple[D5ProfessionalHistoryRecord, ...],
) -> D5ProfessionalHistoryRecord:
    """Select exactly one applicable historical record, never guess."""

    if not isinstance(records, tuple):
        raise D5ProfessionalHistoryError(
            "Immutable history collection required"
        )

    candidates = []
    for record in records:
        if not isinstance(record, D5ProfessionalHistoryRecord):
            raise D5ProfessionalHistoryError(
                "Invalid professional history record"
            )

        # Scope mismatch must not silently become a valid source.
        if (
            record.company_id != source.company_id
            or record.employee_id != position.employee_id
            or record.employment_contract_id
            != source.employment_contract_id
            or record.position_id != position.position_id
        ):
            raise D5ProfessionalHistoryError(
                "Mixed professional history identities"
            )

        if (
            record.effective_from <= source.effective_date
            and (
                record.effective_to is None
                or source.effective_date <= record.effective_to
            )
        ):
            candidates.append(record)

    if len(candidates) != 1:
        raise D5ProfessionalHistoryError(
            "Exactly one effective professional record required"
        )

    selected = candidates[0]
    validate_professional_history(
        source=source,
        position=position,
        record=selected,
    )
    return selected
