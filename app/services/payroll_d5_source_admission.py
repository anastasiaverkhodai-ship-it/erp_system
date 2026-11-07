"""D5 source-file integrity and candidate field-mapping review.

Verifies actual local bytes, declared hashes and mapping provenance.

This module does NOT certify legal authenticity, approve regulatory
codes, or authorize official D5 generation.
"""

from dataclasses import dataclass
from datetime import date
import hashlib
import os
from pathlib import Path
import re
import stat

from app.services.payroll_d5_normative_acceptance import (
    D5NormativeSourceCandidate,
    REQUIRED_SOURCE_KINDS,
    assess_d5_normative_candidates,
)


MAX_SOURCE_BYTES = 25 * 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")

# Internal evidence requirements, NOT official D5 column identifiers.
REVIEW_REQUIREMENTS = frozenset({
    "event_identity",
    "event_effective_date",
    "employment_contract_identity",
    "employment_event_kind",
    "profession_code_provenance",
    "position_history_provenance",
    "termination_legal_basis",
    "reversal_and_correction_provenance",
})


class D5SourceAdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class D5LocalDocument:
    source: D5NormativeSourceCandidate
    relative_path: str


@dataclass(frozen=True)
class D5FieldMappingClaim:
    requirement: str
    document_id: str
    source_reference: str
    effective_from: date
    effective_to: date | None = None


def _document_path(root, relative_path):
    if type(relative_path) is not str or not relative_path:
        raise D5SourceAdmissionError(
            "Relative source path required"
        )

    path = Path(relative_path)

    if (
        path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise D5SourceAdmissionError(
            "Unsafe source path"
        )

    root = Path(root).resolve(strict=True)
    candidate = root.joinpath(*path.parts)

    # Refuse symlinked directories and files.
    current = root

    for part in path.parts:
        current = current / part

        if current.is_symlink():
            raise D5SourceAdmissionError(
                "Symlinked source path prohibited"
            )

    if not candidate.is_relative_to(root):
        raise D5SourceAdmissionError(
            "Source escapes evidence root"
        )

    return candidate


def verify_local_document(root, document):
    """Match one declared digest to genuine bytes on disk."""

    if type(document) is not D5LocalDocument:
        raise D5SourceAdmissionError(
            "Typed document record required"
        )

    assessment = assess_d5_normative_candidates([
        document.source
    ])

    if assessment.candidate_count != 1:
        raise D5SourceAdmissionError(
            "Source declaration rejected"
        )

    path = _document_path(
        root,
        document.relative_path,
    )

    try:
        flags = os.O_RDONLY

        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        fd = os.open(path, flags)

    except OSError as exc:
        raise D5SourceAdmissionError(
            "Source file not accessible"
        ) from exc

    try:
        before = os.fstat(fd)

        if not stat.S_ISREG(before.st_mode):
            raise D5SourceAdmissionError(
                "Regular source file required"
            )

        if before.st_size <= 0:
            raise D5SourceAdmissionError(
                "Empty source file"
            )

        if before.st_size > MAX_SOURCE_BYTES:
            raise D5SourceAdmissionError(
                "Source exceeds size limit"
            )

        digest = hashlib.sha256()
        count = 0

        while True:
            chunk = os.read(fd, 1024 * 1024)

            if not chunk:
                break

            count += len(chunk)

            if count > MAX_SOURCE_BYTES:
                raise D5SourceAdmissionError(
                    "Source exceeds size limit"
                )

            digest.update(chunk)

        after = os.fstat(fd)

        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or count != before.st_size
        ):
            raise D5SourceAdmissionError(
                "Source changed during reading"
            )

        actual_sha256 = digest.hexdigest()

    finally:
        os.close(fd)

    if actual_sha256 != document.source.content_sha256:
        raise D5SourceAdmissionError(
            "Document SHA256 differs from declared source"
        )

    return {
        "document_id": document.source.document_id,
        "source_kind": document.source.source_kind,
        "relative_path": document.relative_path,
        "content_sha256": actual_sha256,
        "size_bytes": count,
        "effective_from": (
            document.source.effective_from.isoformat()
        ),
        "effective_to": (
            document.source.effective_to.isoformat()
            if document.source.effective_to is not None
            else None
        ),
        "local_byte_integrity": "VERIFIED",
        "legal_authenticity": "NOT_APPROVED",
        "normative_field_mapping": "NOT_APPROVED",
        "official_generation": "BLOCKED",
    }


def review_field_mapping_claims(documents, claims):
    """Validate candidate traceability, never approve legal mappings."""

    if type(documents) not in (list, tuple):
        raise D5SourceAdmissionError(
            "Document collection required"
        )

    if type(claims) not in (list, tuple):
        raise D5SourceAdmissionError(
            "Mapping claim collection required"
        )

    if len(documents) > 1000 or len(claims) > 10000:
        raise D5SourceAdmissionError(
            "Review collection limit exceeded"
        )

    sources = {}

    for document in documents:
        if type(document) is not D5LocalDocument:
            raise D5SourceAdmissionError(
                "Typed source declaration required"
            )

        if document.source.document_id in sources:
            raise D5SourceAdmissionError(
                "Duplicate document identity"
            )

        sources[document.source.document_id] = document.source

    if sources:
        assess_d5_normative_candidates(
            [item.source for item in documents]
        )

    covered = set()
    seen = set()

    for claim in claims:
        if type(claim) is not D5FieldMappingClaim:
            raise D5SourceAdmissionError(
                "Typed mapping claim required"
            )

        if claim.requirement not in REVIEW_REQUIREMENTS:
            raise D5SourceAdmissionError(
                "Unknown internal mapping requirement"
            )

        source = sources.get(claim.document_id)

        if source is None:
            raise D5SourceAdmissionError(
                "Mapping has no documented source"
            )

        if (
            not isinstance(claim.source_reference, str)
            or not claim.source_reference.strip()
        ):
            raise D5SourceAdmissionError(
                "Mapping reference required"
            )

        if type(claim.effective_from) is not date:
            raise D5SourceAdmissionError(
                "Mapping effective date required"
            )

        if (
            claim.effective_to is not None
            and (
                type(claim.effective_to) is not date
                or claim.effective_to < claim.effective_from
            )
        ):
            raise D5SourceAdmissionError(
                "Invalid mapping date interval"
            )

        if claim.effective_from < source.effective_from:
            raise D5SourceAdmissionError(
                "Mapping predates source"
            )

        if (
            source.effective_to is not None
            and (
                claim.effective_from > source.effective_to
                or claim.effective_to is None
                or claim.effective_to > source.effective_to
            )
        ):
            raise D5SourceAdmissionError(
                "Mapping exceeds source validity"
            )

        key = (
            claim.requirement,
            claim.document_id,
            claim.source_reference,
            claim.effective_from,
            claim.effective_to,
        )

        if key in seen:
            raise D5SourceAdmissionError(
                "Duplicate mapping claim"
            )

        seen.add(key)
        covered.add(claim.requirement)

    kinds = {
        source.source_kind
        for source in sources.values()
    }

    return {
        "status": "REVIEW_REQUIRED",
        "document_count": len(documents),
        "candidate_mapping_count": len(claims),
        "missing_source_kinds": sorted(
            REQUIRED_SOURCE_KINDS - kinds
        ),
        "missing_mapping_requirements": sorted(
            REVIEW_REQUIREMENTS - covered
        ),
        "legal_source_approval": False,
        "field_mapping_approval": False,
        "official_rows_generated": 0,
        "official_generation": "BLOCKED",
    }
