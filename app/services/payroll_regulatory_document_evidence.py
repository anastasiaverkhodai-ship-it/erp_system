"""Verified bytes and date-bound normative document evidence.

Verifies integrity of locally supplied document artifacts against an
independently recorded expected digest. It does not authenticate the
publisher, verify the legal content, approve mappings, or permit filing.

The existing statutory mapping registry remains authoritative.
"""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
import re

from app.services.payroll_regulatory_official_field_catalogue import (
    FIELD_CANDIDATES,
    validate_catalogue,
)

HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_BYTES = 40 * 1024 * 1024


class DocumentEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class DocumentEvidence:
    document_id: str
    source_url: str
    revision_reference: str
    expected_sha256: str
    effective_from: date
    effective_to: date | None
    reviewed_on: date
    field_keys: tuple[str, ...]
    reviewer_reference: str


def validate_record(record: DocumentEvidence):
    if not isinstance(record, DocumentEvidence):
        raise DocumentEvidenceError("DocumentEvidence required")

    for item in (
        record.document_id,
        record.revision_reference,
        record.reviewer_reference,
    ):
        if not isinstance(item, str) or not item.strip():
            raise DocumentEvidenceError(
                "Document identification or review reference missing"
            )

    sources = {row.source_url for row in FIELD_CANDIDATES}
    if record.source_url not in sources:
        raise DocumentEvidenceError(
            "Unknown catalogue source URL"
        )

    if not isinstance(record.expected_sha256, str) or not (
        HEX64.fullmatch(record.expected_sha256)
    ):
        raise DocumentEvidenceError(
            "Expected SHA256 must be 64 lowercase hex characters"
        )

    if (
        type(record.effective_from) is not date
        or type(record.reviewed_on) is not date
    ):
        raise DocumentEvidenceError(
            "Explicit effective and review dates required"
        )

    if record.effective_to is not None and (
        type(record.effective_to) is not date
        or record.effective_to < record.effective_from
    ):
        raise DocumentEvidenceError(
            "Invalid effective-date interval"
        )

    if (
        not isinstance(record.field_keys, tuple)
        or not record.field_keys
        or any(not isinstance(key, str) or not key for key in record.field_keys)
        or len(record.field_keys) != len(set(record.field_keys))
    ):
        raise DocumentEvidenceError(
            "Unique non-empty field references required"
        )

    validate_catalogue()

    valid_fields = {
        row.field_key
        for row in FIELD_CANDIDATES
        if row.source_url == record.source_url
    }

    if not set(record.field_keys) <= valid_fields:
        raise DocumentEvidenceError(
            "Field does not match its source anchor"
        )

    return record


def verify_document_bytes(
    record: DocumentEvidence,
    content: bytes,
    *,
    period_start: date,
    period_end: date,
):
    """Verify actual bytes, provenance anchors and date interval.

    SHA256 match is an integrity check, not publisher authentication.
    """

    validate_record(record)

    if (
        type(period_start) is not date
        or type(period_end) is not date
        or period_start > period_end
    ):
        raise DocumentEvidenceError(
            "Invalid reporting interval"
        )

    if not isinstance(content, bytes) or not content:
        raise DocumentEvidenceError(
            "Actual non-empty document bytes required"
        )

    if len(content) > MAX_BYTES:
        raise DocumentEvidenceError(
            "Document exceeds verification size limit"
        )

    actual_digest = sha256(content).hexdigest()
    if actual_digest != record.expected_sha256:
        raise DocumentEvidenceError(
            "Document SHA256 mismatch"
        )

    if (
        record.effective_from > period_start
        or (
            record.effective_to is not None
            and record.effective_to < period_end
        )
    ):
        raise DocumentEvidenceError(
            "Document revision does not cover reporting period"
        )

    return {
        "document_id": record.document_id,
        "sha256": actual_digest,
        "byte_length": len(content),
        "field_keys": list(record.field_keys),
        "integrity": "HASH_MATCH",
        "source_authenticity": "NOT_ESTABLISHED_BY_HASH",
        "legal_content_approval": "NOT_ESTABLISHED",
        "official_generation": "BLOCKED",
    }


def verify_document_file(
    record: DocumentEvidence,
    *,
    root: Path,
    relative_path: str,
    period_start: date,
    period_end: date,
):
    """Read files strictly beneath a trusted evidence directory."""

    validate_record(record)

    if not isinstance(root, Path):
        raise DocumentEvidenceError("Path root required")

    if not isinstance(relative_path, str) or not relative_path:
        raise DocumentEvidenceError("Relative document path required")

    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DocumentEvidenceError(
            "Absolute paths and path traversal forbidden"
        )

    base = root.resolve(strict=True)
    path = base.joinpath(candidate)

    # Do not allow symbolic links anywhere in the relative chain.
    current = base
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise DocumentEvidenceError(
                "Symlink evidence paths are forbidden"
            )

    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise DocumentEvidenceError(
            "Evidence file must be within trusted root"
        )

    if resolved.stat().st_size > MAX_BYTES:
        raise DocumentEvidenceError("Evidence file is too large")

    return verify_document_bytes(
        record,
        resolved.read_bytes(),
        period_start=period_start,
        period_end=period_end,
    )


def verify_document_collection(
    records_with_bytes,
    *,
    period_start: date,
    period_end: date,
):
    """Validate a collection; reject duplicate or conflicting evidence."""

    results = []
    seen_ids = set()
    seen_fields = set()

    for record, content in records_with_bytes:
        if record.document_id in seen_ids:
            raise DocumentEvidenceError(
                "Duplicate document identity"
            )
        seen_ids.add(record.document_id)

        result = verify_document_bytes(
            record,
            content,
            period_start=period_start,
            period_end=period_end,
        )

        for field in record.field_keys:
            if field in seen_fields:
                raise DocumentEvidenceError(
                    "Ambiguous document coverage for field"
                )
            seen_fields.add(field)

        results.append(result)

    return {
        "documents_integrity_checked": len(results),
        "covered_candidate_fields": sorted(seen_fields),
        "artifacts": results,
        "source_authenticity_verified": False,
        "normative_field_approval": False,
        "official_generation": "BLOCKED",
    }
