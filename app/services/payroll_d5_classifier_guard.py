
"""Fail-closed verification of profession classification evidence.

Does not fetch or manufacture official classifier entries.
An approved, independently reviewed registry must supply the records.
"""
import re
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from app.services.payroll_d5_professional_history import (
    D5ProfessionalHistoryRecord,
)


class D5ClassifierError(ValueError):
    pass


@dataclass(frozen=True)
class D5ClassifierEntry:
    profession_code: str
    professional_name: str
    effective_from: date
    effective_to: date | None
    source_reference: str
    source_version: str
    reviewed: bool


@dataclass(frozen=True)
class D5ClassifierVerification:
    profession_code: str
    professional_name: str
    event_date: date
    source_reference: str
    source_version: str


def _clean(value, name):
    if not isinstance(value, str) or not value.strip():
        raise D5ClassifierError(f"Missing {name}")
    return value.strip()


def verify_d5_classifier(
    *,
    history: D5ProfessionalHistoryRecord,
    event_date: date,
    entries: Sequence[D5ClassifierEntry],
) -> D5ClassifierVerification:
    if not isinstance(history, D5ProfessionalHistoryRecord):
        raise D5ClassifierError("Typed professional history required")

    if type(event_date) is not date:
        raise D5ClassifierError("Valid event date required")

    if not isinstance(entries, (tuple, list)):
        raise D5ClassifierError("Explicit reviewed registry required")

    requested_code = _clean(history.profession_code, "profession code")
    requested_name = _clean(
        history.professional_name, "professional name"
    )

    if re.fullmatch(r"[0-9]{4}(?:\.[0-9])?", requested_code) is None:
        raise D5ClassifierError(
            "Invalid profession classification code format"
        )

    if not entries:
        raise D5ClassifierError("No reviewed classifier registry loaded")

    matched = []

    for entry in entries:
        if not isinstance(entry, D5ClassifierEntry):
            raise D5ClassifierError("Invalid classifier entry type")

        if type(entry.effective_from) is not date:
            raise D5ClassifierError("Invalid classifier start date")

        if entry.effective_to is not None:
            if type(entry.effective_to) is not date:
                raise D5ClassifierError("Invalid classifier end date")
            if entry.effective_to < entry.effective_from:
                raise D5ClassifierError("Invalid classifier date interval")

        code = _clean(entry.profession_code, "registry code")
        name = _clean(entry.professional_name, "registry profession")
        reference = _clean(entry.source_reference, "official source")
        version = _clean(entry.source_version, "source version")

        if not entry.reviewed:
            continue

        if (
            code == requested_code
            and name == requested_name
            and entry.effective_from <= event_date
            and (
                entry.effective_to is None
                or event_date <= entry.effective_to
            )
        ):
            matched.append((entry, reference, version))

    if len(matched) != 1:
        raise D5ClassifierError(
            "Exactly one reviewed effective classifier entry required"
        )

    entry, reference, version = matched[0]

    return D5ClassifierVerification(
        profession_code=entry.profession_code,
        professional_name=entry.professional_name,
        event_date=event_date,
        source_reference=reference,
        source_version=version,
    )
