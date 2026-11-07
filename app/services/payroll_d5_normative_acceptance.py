"""D5 normative source admission — review-only, fail-closed.

Candidate metadata is not evidence of normative approval.
No statutory rows are generated or authorized by this module.
"""

from dataclasses import dataclass
from datetime import date
import re
from urllib.parse import urlsplit


class D5NormativeEvidenceError(ValueError):
    pass


REQUIRED_SOURCE_KINDS = frozenset({
    "profession_classifier",
    "classifier_amendments",
    "d5_filing_rules",
    "d5_field_mapping",
    "d5_correction_rules",
})

APPROVED_SOURCE_DOCUMENTS = ()

BLOCKER = "D5_NORMATIVE_SOURCE_CHAIN_NOT_APPROVED"

HASH_PATTERN = re.compile(r"[a-f0-9]{64}\Z")


@dataclass(frozen=True)
class D5NormativeSourceCandidate:
    document_id: str
    source_kind: str
    source_url: str
    content_sha256: str
    effective_from: date
    effective_to: date | None
    review_reference: str


@dataclass(frozen=True)
class D5NormativeAssessment:
    status: str
    official_generation: str
    official_rows_generated: int
    candidate_count: int
    missing_source_kinds: tuple[str, ...]
    conflicting_document_ids: tuple[str, ...]
    blockers: tuple[str, ...]


def require_text(value, field):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
    ):
        raise D5NormativeEvidenceError(
            "Invalid documented field: " + field
        )


def validate_candidate(candidate):
    if type(candidate) is not D5NormativeSourceCandidate:
        raise D5NormativeEvidenceError(
            "Typed source candidate required"
        )

    require_text(candidate.document_id, "document_id")
    require_text(candidate.review_reference, "review_reference")

    if candidate.source_kind not in REQUIRED_SOURCE_KINDS:
        raise D5NormativeEvidenceError(
            "Unsupported source kind"
        )

    require_text(candidate.source_url, "source_url")

    parsed = urlsplit(candidate.source_url)

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise D5NormativeEvidenceError(
            "Canonical HTTPS source URL required"
        )

    if (
        type(candidate.content_sha256) is not str
        or HASH_PATTERN.fullmatch(
            candidate.content_sha256
        ) is None
        or candidate.content_sha256 == "0" * 64
    ):
        raise D5NormativeEvidenceError(
            "Valid-format SHA256 required"
        )

    if type(candidate.effective_from) is not date:
        raise D5NormativeEvidenceError(
            "Effective-from date required"
        )

    if candidate.effective_to is not None:
        if (
            type(candidate.effective_to) is not date
            or candidate.effective_to
            < candidate.effective_from
        ):
            raise D5NormativeEvidenceError(
                "Invalid effective date interval"
            )

    return candidate


def assess_d5_normative_candidates(candidates=()):
    """Inspect source metadata without issuing approvals."""

    if type(candidates) not in (tuple, list):
        raise D5NormativeEvidenceError(
            "Source collection must be a list or tuple"
        )

    if len(candidates) > 1000:
        raise D5NormativeEvidenceError(
            "Candidate limit exceeded"
        )

    by_document = {}
    seen = set()
    found = set()
    conflicts = set()

    for candidate in candidates:
        validate_candidate(candidate)

        identity = (
            candidate.document_id,
            candidate.source_kind,
            candidate.content_sha256,
            candidate.effective_from,
            candidate.effective_to,
        )

        if identity in seen:
            raise D5NormativeEvidenceError(
                "Duplicate evidence candidate"
            )

        seen.add(identity)
        found.add(candidate.source_kind)

        previous = by_document.get(candidate.document_id)

        if previous is None:
            by_document[candidate.document_id] = candidate
        elif (
            previous.source_kind != candidate.source_kind
            or previous.content_sha256
            != candidate.content_sha256
            or previous.effective_from
            != candidate.effective_from
            or previous.effective_to
            != candidate.effective_to
        ):
            conflicts.add(candidate.document_id)

    missing = tuple(
        sorted(REQUIRED_SOURCE_KINDS - found)
    )

    blockers = [BLOCKER]

    if missing:
        blockers.append(
            "D5_NORMATIVE_SOURCE_COVERAGE_INCOMPLETE"
        )

    if conflicts:
        blockers.append(
            "D5_NORMATIVE_SOURCE_CONFLICT"
        )

    return D5NormativeAssessment(
        status="REVIEW_REQUIRED",
        official_generation="BLOCKED",
        official_rows_generated=0,
        candidate_count=len(candidates),
        missing_source_kinds=missing,
        conflicting_document_ids=tuple(
            sorted(conflicts)
        ),
        blockers=tuple(blockers),
    )
