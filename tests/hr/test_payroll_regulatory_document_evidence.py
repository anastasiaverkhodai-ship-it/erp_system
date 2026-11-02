from dataclasses import replace
from datetime import date
from hashlib import sha256

import pytest

from app.services.payroll_regulatory_official_field_catalogue import (
    FIELD_CANDIDATES,
)
from app.services.payroll_regulatory_document_evidence import (
    DocumentEvidence,
    DocumentEvidenceError,
    verify_document_bytes,
    verify_document_file,
    verify_document_collection,
)


PERIOD_START = date(2026, 10, 1)
PERIOD_END = date(2026, 10, 31)
CONTENT = b"SYNTHETIC TEST DOCUMENT - NOT OFFICIAL"


@pytest.fixture
def record():
    candidate = FIELD_CANDIDATES[0]
    return DocumentEvidence(
        document_id="TEST-4DF-OCT-2026",
        source_url=candidate.source_url,
        revision_reference="TEST-ONLY-REVISION",
        expected_sha256=sha256(CONTENT).hexdigest(),
        effective_from=date(2026, 1, 1),
        effective_to=None,
        reviewed_on=date(2026, 10, 1),
        field_keys=(candidate.field_key,),
        reviewer_reference="TEST-ONLY",
    )


def checked(record, content=CONTENT):
    return verify_document_bytes(
        record,
        content,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )


def test_exact_bytes_pass_integrity_only(record):
    result = checked(record)
    assert result["integrity"] == "HASH_MATCH"
    assert result["official_generation"] == "BLOCKED"
    assert result["legal_content_approval"] == "NOT_ESTABLISHED"
    assert result["source_authenticity"] == "NOT_ESTABLISHED_BY_HASH"


def test_modified_document_rejected(record):
    with pytest.raises(DocumentEvidenceError, match="SHA256"):
        checked(record, CONTENT + b" alteration")


def test_empty_document_rejected(record):
    with pytest.raises(DocumentEvidenceError, match="non-empty"):
        checked(record, b"")


def test_incomplete_effective_coverage_rejected(record):
    limited = replace(
        record,
        effective_to=date(2026, 10, 15),
    )
    with pytest.raises(DocumentEvidenceError, match="cover"):
        checked(limited)


def test_effective_start_after_period_rejected(record):
    future = replace(
        record,
        effective_from=date(2026, 11, 1),
    )
    with pytest.raises(DocumentEvidenceError, match="cover"):
        checked(future)


def test_wrong_source_anchor_rejected(record):
    other = next(
        row for row in FIELD_CANDIDATES
        if row.source_url != record.source_url
    )
    with pytest.raises(DocumentEvidenceError, match="source anchor"):
        checked(replace(record, field_keys=(other.field_key,)))


def test_missing_review_reference_rejected(record):
    with pytest.raises(DocumentEvidenceError):
        checked(replace(record, reviewer_reference=""))


def test_invalid_sha_rejected(record):
    with pytest.raises(DocumentEvidenceError, match="SHA256"):
        checked(replace(record, expected_sha256="abc"))


def test_bad_interval_rejected(record):
    with pytest.raises(DocumentEvidenceError):
        checked(replace(
            record,
            effective_to=date(2025, 1, 1),
        ))


def test_unique_collection(record):
    result = verify_document_collection(
        [(record, CONTENT)],
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )
    assert result["documents_integrity_checked"] == 1
    assert result["normative_field_approval"] is False
    assert result["official_generation"] == "BLOCKED"


def test_duplicate_document_rejected(record):
    with pytest.raises(DocumentEvidenceError, match="Duplicate"):
        verify_document_collection(
            [(record, CONTENT), (record, CONTENT)],
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )


def test_conflicting_field_coverage_rejected(record):
    second = replace(record, document_id="TEST-SECOND")
    with pytest.raises(DocumentEvidenceError, match="Ambiguous"):
        verify_document_collection(
            [(record, CONTENT), (second, CONTENT)],
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )


def test_real_local_file_hash(tmp_path, record):
    (tmp_path / "document.bin").write_bytes(CONTENT)
    result = verify_document_file(
        record,
        root=tmp_path,
        relative_path="document.bin",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )
    assert result["sha256"] == sha256(CONTENT).hexdigest()


def test_path_traversal_rejected(tmp_path, record):
    with pytest.raises(DocumentEvidenceError, match="traversal"):
        verify_document_file(
            record,
            root=tmp_path,
            relative_path="../document.bin",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )


def test_symlink_document_rejected(tmp_path, record):
    target = tmp_path / "real.bin"
    target.write_bytes(CONTENT)
    (tmp_path / "alias.bin").symlink_to(target)

    with pytest.raises(DocumentEvidenceError, match="Symlink"):
        verify_document_file(
            record,
            root=tmp_path,
            relative_path="alias.bin",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )


def test_empty_collection_remains_blocked():
    result = verify_document_collection(
        [],
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )
    assert result["documents_integrity_checked"] == 0
    assert result["official_generation"] == "BLOCKED"
