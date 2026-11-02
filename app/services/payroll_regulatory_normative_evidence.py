"""Fail-closed statutory normative evidence review.

This module records and validates proposed normative mappings.
It does NOT approve mappings, determine official form fields, or
authorize statutory report generation.

APPROVED_MAPPINGS and D5 APPROVED_SOURCES remain authoritative
for production generation.
"""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import re

KINDS = frozenset({"d1", "4df", "d5", "d6"})
STATES = frozenset({
    "candidate",
    "source_verified",
    "approved",
})

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class NormativeEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class NormativeEvidence:
    evidence_id: str
    report_kind: str
    field_key: str
    source_title: str
    source_url: str
    source_sha256: str
    source_effective_from: date
    source_effective_to: date | None
    reviewed_at: date | None
    approved_at: date | None
    approval_reference: str | None
    state: str


def validate_evidence(evidence: NormativeEvidence) -> None:
    if not isinstance(evidence, NormativeEvidence):
        raise NormativeEvidenceError("Evidence object required")

    for value, label in (
        (evidence.evidence_id, "Evidence ID"),
        (evidence.field_key, "Field key"),
        (evidence.source_title, "Source title"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise NormativeEvidenceError(f"{label} required")

    if evidence.report_kind not in KINDS:
        raise NormativeEvidenceError("Unsupported statutory report kind")

    if evidence.state not in STATES:
        raise NormativeEvidenceError("Invalid evidence state")

    if (
        not isinstance(evidence.source_url, str)
        or not evidence.source_url.startswith("https://")
    ):
        raise NormativeEvidenceError("HTTPS source URL required")

    if not SHA256_PATTERN.fullmatch(
        evidence.source_sha256 or ""
    ):
        raise NormativeEvidenceError(
            "Verified source SHA256 required"
        )

    if not isinstance(evidence.source_effective_from, date):
        raise NormativeEvidenceError(
            "Source effective-from date required"
        )

    if (
        evidence.source_effective_to is not None
        and (
            not isinstance(evidence.source_effective_to, date)
            or evidence.source_effective_to
            < evidence.source_effective_from
        )
    ):
        raise NormativeEvidenceError(
            "Invalid source effective-date interval"
        )

    if (
        evidence.reviewed_at is not None
        and not isinstance(evidence.reviewed_at, date)
    ):
        raise NormativeEvidenceError("Invalid review date")

    if (
        evidence.approved_at is not None
        and not isinstance(evidence.approved_at, date)
    ):
        raise NormativeEvidenceError("Invalid approval date")

    if evidence.state in {"source_verified", "approved"}:
        if evidence.reviewed_at is None:
            raise NormativeEvidenceError(
                "Source verification requires review date"
            )

    if evidence.state == "approved":
        if evidence.approved_at is None:
            raise NormativeEvidenceError(
                "Approved evidence requires approval date"
            )
        if not evidence.approval_reference or not (
            evidence.approval_reference.strip()
        ):
            raise NormativeEvidenceError(
                "Approved evidence requires approval reference"
            )
        if evidence.approved_at < evidence.reviewed_at:
            raise NormativeEvidenceError(
                "Approval cannot precede review"
            )
    elif (
        evidence.approved_at is not None
        or evidence.approval_reference is not None
    ):
        raise NormativeEvidenceError(
            "Unapproved evidence cannot contain approval metadata"
        )


def evidence_for_period(
    records,
    *,
    report_kind: str,
    field_key: str,
    period_start: date,
    period_end: date,
):
    """Return only valid, approved, date-covering evidence.

    Coverage means the same source applies to the whole period.
    Split effective-date intervals require separate review.
    """
    if report_kind not in KINDS:
        raise NormativeEvidenceError("Invalid report kind")

    if not isinstance(field_key, str) or not field_key:
        raise NormativeEvidenceError("Field key required")

    if (
        not isinstance(period_start, date)
        or not isinstance(period_end, date)
        or period_start > period_end
    ):
        raise NormativeEvidenceError("Invalid reporting period")

    candidates = []

    for record in records:
        validate_evidence(record)

        if (
            record.report_kind == report_kind
            and record.field_key == field_key
            and record.state == "approved"
            and record.source_effective_from <= period_start
            and (
                record.source_effective_to is None
                or record.source_effective_to >= period_end
            )
        ):
            candidates.append(record)

    if len(candidates) > 1:
        raise NormativeEvidenceError(
            "Ambiguous approved normative mapping"
        )

    return candidates[0] if candidates else None


def fingerprint_source_bytes(source: bytes) -> str:
    if not isinstance(source, bytes) or not source:
        raise NormativeEvidenceError(
            "Non-empty source document bytes required"
        )
    return sha256(source).hexdigest()


def normative_gate_status(records, *, start: date, end: date):
    """Review-only overview; does not authorize finalization."""
    records = tuple(records)

    for record in records:
        validate_evidence(record)

    details = {}

    for kind in sorted(KINDS):
        relevant = [
            record for record in records
            if record.report_kind == kind
        ]

        fields = sorted({
            record.field_key for record in relevant
        })

        approved_fields = []

        for field in fields:
            approved = evidence_for_period(
                relevant,
                report_kind=kind,
                field_key=field,
                period_start=start,
                period_end=end,
            )

            if approved is not None:
                approved_fields.append(field)

        details[kind] = {
            "registered_evidence": len(relevant),
            "approved_field_evidence": len(approved_fields),
            "candidate_or_pending": sum(
                record.state != "approved"
                for record in relevant
            ),
            "covered_field_keys": approved_fields,
            "official_generation": "BLOCKED",
        }

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "forms": details,
        "official_generation": "BLOCKED",
    }
