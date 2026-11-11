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
            candidate.source_url,
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
            or previous.source_url != candidate.source_url
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


def review_d5_sources_for_period(candidates, reporting_date):
    """Review date-effective source coverage; never approve filing.

    Multiple active sources of one kind are reported as ambiguous,
    not silently selected as an authoritative version.
    """
    if type(reporting_date) is not date:
        raise D5NormativeEvidenceError(
            "Exact reporting date required"
        )

    assessment = assess_d5_normative_candidates(candidates)

    active = {
        kind: []
        for kind in sorted(REQUIRED_SOURCE_KINDS)
    }

    for candidate in candidates:
        if (
            candidate.effective_from <= reporting_date
            and (
                candidate.effective_to is None
                or reporting_date <= candidate.effective_to
            )
        ):
            active[candidate.source_kind].append(
                candidate.document_id
            )

    missing = tuple(
        kind for kind, ids in active.items()
        if not ids
    )

    ambiguous = tuple(
        kind for kind, ids in active.items()
        if len(ids) > 1
    )

    blockers = list(assessment.blockers)

    if missing:
        blockers.append(
            "D5_REPORTING_PERIOD_SOURCE_COVERAGE_INCOMPLETE"
        )

    if ambiguous:
        blockers.append(
            "D5_REPORTING_PERIOD_SOURCE_VERSION_AMBIGUOUS"
        )

    return {
        "reporting_date": reporting_date.isoformat(),
        "active_source_ids": {
            kind: tuple(ids)
            for kind, ids in active.items()
        },
        "missing_source_kinds": missing,
        "ambiguous_source_kinds": ambiguous,
        "conflicting_document_ids": (
            assessment.conflicting_document_ids
        ),
        "blockers": tuple(dict.fromkeys(blockers)),
        "status": "REVIEW_REQUIRED",
        "normative_mapping": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }

def review_d5_annex_normative_bundle(
    *,
    source_root,
    manifest_paths,
    candidates,
    reporting_date,
    source_documents=None,
):
    """Cross-check annex bytes, amendment events and normative metadata.

    A matching candidate digest is metadata consistency, not proof
    of document authenticity or permission to generate a filing.
    """
    from hashlib import sha256
    import json

    from app.services.payroll_d5_annex_chain_review import (
        review_d5_annex_chain,
    )
    from app.services.payroll_d5_annex_event_reconciliation import (
        reconcile_d5_annex_events,
    )

    # Validate source-candidate structure and period coverage first.
    period = review_d5_sources_for_period(
        candidates,
        reporting_date,
    )

    chain = review_d5_annex_chain(
        source_root=source_root,
        manifest_paths=manifest_paths,
    )

    events = reconcile_d5_annex_events(
        source_root=source_root,
        manifest_paths=manifest_paths,
    )

    if events["chain_sha256"] != chain["chain_sha256"]:
        raise D5NormativeEvidenceError(
            "D5 amendment chain digest mismatch"
        )

    amendment_candidates = [
        item for item in candidates
        if item.source_kind == "classifier_amendments"
    ]

    # Verify actual source-document bytes, not just matching
    # SHA-256 strings supplied by two separate manifests.
    if source_documents is None:
        source_documents = {}

    if type(source_documents) is not dict:
        raise D5NormativeEvidenceError(
            "Source documents must be keyed by amendment number"
        )

    expected_numbers = {
        document["number"] for document in chain["documents"]
    }

    if any(
        type(number) is not int or number not in expected_numbers
        for number in source_documents
    ):
        raise D5NormativeEvidenceError(
            "Unknown source document amendment"
        )

    source_checks = []
    verified_numbers = set()

    for document in chain["documents"]:
        number = document["number"]

        if number not in source_documents:
            source_checks.append({
                "amendment_number": number,
                "status": "SOURCE_BYTES_NOT_PROVIDED",
            })
            continue

        actual_digest = _verify_d5_source_bytes(
            source_root,
            source_documents[number],
        )

        if actual_digest != document["source_sha256"]:
            raise D5NormativeEvidenceError(
                "D5 source document SHA256 mismatch"
            )

        verified_numbers.add(number)
        source_checks.append({
            "amendment_number": number,
            "status": "LOCAL_SOURCE_BYTES_MATCH",
            "content_sha256": actual_digest,
        })

    unverified_numbers = tuple(
        sorted(expected_numbers - verified_numbers)
    )

    source_status_by_number = {
        item["amendment_number"]: item["status"]
        for item in source_checks
    }

    bindings = []
    missing = []
    ambiguous = []

    for document in chain["documents"]:
        effective = date.fromisoformat(
            document["effective_date"]
        )

        matching = sorted(
            (
                item.document_id
                for item in amendment_candidates
                if (
                    item.content_sha256
                    == document["source_sha256"]
                    and item.effective_from <= effective
                    and (
                        item.effective_to is None
                        or effective <= item.effective_to
                    )
                )
            )
        )

        if not matching:
            status = "CANDIDATE_SOURCE_BINDING_MISSING"
            missing.append(document["number"])
        elif len(matching) > 1:
            status = "CANDIDATE_SOURCE_BINDING_AMBIGUOUS"
            ambiguous.append(document["number"])
        else:
            status = (
                "CANDIDATE_SOURCE_BYTES_MATCH_ONLY"
                if document["number"] in verified_numbers
                else "CANDIDATE_METADATA_MATCH_ONLY"
            )

        bindings.append({
            "amendment_number": document["number"],
            "effective_date": document["effective_date"],
            "source_sha256": document["source_sha256"],
            "candidate_document_ids": tuple(matching),
            "status": status,
            "source_bytes_status": source_status_by_number[
                document["number"]
            ],
        })

    blockers = list(dict.fromkeys(
        list(chain["blockers"])
        + list(events["blockers"])
        + list(period["blockers"])
    ))

    if unverified_numbers:
        blockers.append(
            "D5_ORIGINAL_SOURCE_BYTES_NOT_VERIFIED"
        )

    if missing:
        blockers.append(
            "D5_AMENDMENT_SOURCE_CANDIDATE_BINDING_MISSING"
        )

    if ambiguous:
        blockers.append(
            "D5_AMENDMENT_SOURCE_CANDIDATE_BINDING_AMBIGUOUS"
        )

    payload = {
        "schema": "D5_ANNEX_NORMATIVE_BUNDLE_V1",
        "reporting_date": period["reporting_date"],
        "chain_sha256": chain["chain_sha256"],
        "event_reconciliation_sha256": (
            events["reconciliation_sha256"]
        ),
        "bindings": bindings,
        "source_document_checks": source_checks,
        "unverified_source_amendments": list(unverified_numbers),
        "missing_binding_amendments": missing,
        "ambiguous_binding_amendments": ambiguous,
        "period_missing_source_kinds": list(
            period["missing_source_kinds"]
        ),
        "period_ambiguous_source_kinds": list(
            period["ambiguous_source_kinds"]
        ),
    }

    digest = sha256(
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    return {
        **payload,
        "bundle_sha256": digest,
        "amendment_count": chain["amendment_count"],
        "candidate_event_count": chain["candidate_event_count"],
        "event_conflict_count": len(events["issues"]),
        "missing_amendment_numbers": (
            chain["missing_amendment_numbers"]
        ),
        "missing_binding_amendments": tuple(missing),
        "ambiguous_binding_amendments": tuple(ambiguous),
        "bindings": tuple(bindings),
        "status": "REVIEW_REQUIRED",
        "source_binding": (
            "LOCAL_SOURCE_BYTES_MATCH_ONLY"
            if not unverified_numbers
            else "CANDIDATE_METADATA_ONLY"
        ),
        "independent_authenticity": "NOT_VERIFIED",
        "full_classifier_history": "NOT_VERIFIED",
        "normative_mapping": "NOT_APPROVED",
        "blockers": tuple(dict.fromkeys(blockers)),
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }



def _verify_d5_source_bytes(root, relative_path):
    """Read a bounded local original without allowing path escape."""
    import os
    import stat
    from pathlib import Path
    from hashlib import sha256

    if type(relative_path) is not str or not relative_path:
        raise D5NormativeEvidenceError(
            "Invalid original source document path"
        )

    relative = Path(relative_path)

    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
    ):
        raise D5NormativeEvidenceError(
            "Original source must remain inside evidence root"
        )

    try:
        root = Path(root).resolve(strict=True)
        current = root

        for component in relative.parts:
            current = current / component

            if current.is_symlink():
                raise D5NormativeEvidenceError(
                    "Symlinked original evidence is forbidden"
                )

        resolved = current.resolve(strict=True)

        if not resolved.is_relative_to(root):
            raise D5NormativeEvidenceError(
                "Original source escaped evidence root"
            )

        flags = os.O_RDONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)

        descriptor = os.open(resolved, flags)

        try:
            info = os.fstat(descriptor)

            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size < 1
                or info.st_size > 25 * 1024 * 1024
            ):
                raise D5NormativeEvidenceError(
                    "Invalid original source file size or type"
                )

            digest = sha256()
            total = 0

            while True:
                chunk = os.read(descriptor, 1024 * 1024)

                if not chunk:
                    break

                total += len(chunk)

                if total > 25 * 1024 * 1024:
                    raise D5NormativeEvidenceError(
                        "Original source exceeds byte limit"
                    )

                digest.update(chunk)

            return digest.hexdigest()
        finally:
            os.close(descriptor)

    except (OSError, ValueError) as exc:
        if isinstance(exc, D5NormativeEvidenceError):
            raise

        raise D5NormativeEvidenceError(
            "Original source file cannot be verified"
        ) from exc
