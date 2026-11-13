
"""Fail-closed classifier import with pinned source approval.

SHA-256 proves byte integrity against an approved digest, not that
the underlying document is official. Approval is a separate review.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date

from app.services.payroll_d5_classifier_guard import (
    D5ClassifierEntry,
    D5ClassifierError,
)


class D5TrustedClassifierError(D5ClassifierError):
    pass


@dataclass(frozen=True)
class ApprovedClassifierSource:
    sha256: str
    source_reference: str
    source_version: str


# Populate only through separately reviewed source-control changes.
# Never derive approval from the imported file itself.
# Review: docs/normative_evidence/2026_11_13/APPROVAL.md.
# Only civil profession identities/intervals are approved, not D5 filing fields.
APPROVED_SOURCES: tuple[ApprovedClassifierSource, ...] = (
    ApprovedClassifierSource(
        sha256="5c08794bcd7212cf8baaee5bbdb6f9642b5654868b0e261bdfa438f0b30f4a1c",
        source_reference="DK003:2010 civil sections 1-9; Ministry consolidated XLS 2023 and amendments 12-17",
        source_version="civil-2026-a17-20261112",
    ),
)


def import_approved_classifier(
    raw: bytes,
) -> tuple[D5ClassifierEntry, ...]:
    if not isinstance(raw, bytes) or not raw:
        raise D5TrustedClassifierError("Nonempty source bytes required")

    digest = hashlib.sha256(raw).hexdigest()

    approvals = [
        item for item in APPROVED_SOURCES
        if item.sha256 == digest
    ]
    if len(approvals) != 1:
        raise D5TrustedClassifierError(
            "Source digest has no unique independent approval"
        )

    approval = approvals[0]

    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise D5TrustedClassifierError(
            "Invalid classifier JSON"
        ) from exc

    if not isinstance(document, dict):
        raise D5TrustedClassifierError("Object document required")

    if set(document) != {"source_reference", "source_version", "entries"}:
        raise D5TrustedClassifierError(
            "Unexpected classifier document fields"
        )

    if (
        document["source_reference"] != approval.source_reference
        or document["source_version"] != approval.source_version
    ):
        raise D5TrustedClassifierError(
            "Source identity does not match approved digest"
        )

    records = document["entries"]
    if not isinstance(records, list) or not records:
        raise D5TrustedClassifierError(
            "Nonempty classifier entries required"
        )

    result = []
    seen = set()

    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "profession_code", "professional_name",
            "effective_from", "effective_to",
        }:
            raise D5TrustedClassifierError(
                "Invalid classifier entry structure"
            )

        code = record["profession_code"]
        name = record["professional_name"]

        if (
            not isinstance(code, str)
            or re.fullmatch(r"[0-9]{4}(?:\.[0-9])?", code) is None
            or not isinstance(name, str)
            or not name.strip()
        ):
            raise D5TrustedClassifierError(
                "Invalid code or profession name"
            )

        try:
            start = date.fromisoformat(record["effective_from"])
            end = (
                date.fromisoformat(record["effective_to"])
                if record["effective_to"] is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise D5TrustedClassifierError(
                "Invalid effective dates"
            ) from exc

        if end is not None and end < start:
            raise D5TrustedClassifierError(
                "Invalid effective interval"
            )

        key = (code, name.strip(), start, end)
        if key in seen:
            raise D5TrustedClassifierError(
                "Duplicate classifier entry"
            )
        seen.add(key)

        result.append(D5ClassifierEntry(
            profession_code=code,
            professional_name=name.strip(),
            effective_from=start,
            effective_to=end,
            source_reference=approval.source_reference,
            source_version=approval.source_version,
            reviewed=True,
        ))

    # Reject overlapping validity for the same classification identity.
    by_identity = {}
    for entry in result:
        # A classification group contains multiple simultaneous professions.
        # Only versions of the same code/name pair can overlap ambiguously.
        identity = (entry.profession_code, entry.professional_name)
        by_identity.setdefault(identity, []).append(entry)

    for records in by_identity.values():
        records.sort(key=lambda value: value.effective_from)
        for previous, current in zip(records, records[1:]):
            if (
                previous.effective_to is None
                or previous.effective_to >= current.effective_from
            ):
                raise D5TrustedClassifierError(
                    "Overlapping classifier validity"
                )

    return tuple(result)
