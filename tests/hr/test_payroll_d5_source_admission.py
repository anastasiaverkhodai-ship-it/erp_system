from dataclasses import replace
from datetime import date
import hashlib

import pytest

from app.services.payroll_d5_normative_acceptance import (
    D5NormativeSourceCandidate,
    REQUIRED_SOURCE_KINDS,
)
from app.services.payroll_d5_source_admission import (
    D5FieldMappingClaim,
    D5LocalDocument,
    D5SourceAdmissionError,
    REVIEW_REQUIREMENTS,
    review_field_mapping_claims,
    verify_local_document,
)


def document(content, name="source.txt", kind="d5_filing_rules"):
    return D5LocalDocument(
        source=D5NormativeSourceCandidate(
            document_id="DOCUMENT-1",
            source_kind=kind,
            source_url="https://example.org/source",
            content_sha256=hashlib.sha256(content).hexdigest(),
            effective_from=date(2026, 1, 1),
            effective_to=None,
            review_reference="TEST-ONLY-UNAPPROVED",
        ),
        relative_path=name,
    )


def claim(
    requirement="event_identity",
    document_id="DOCUMENT-1",
):
    return D5FieldMappingClaim(
        requirement=requirement,
        document_id=document_id,
        source_reference="TEST-SECTION-1",
        effective_from=date(2026, 1, 1),
    )


def test_real_file_hash_verified_without_approval(tmp_path):
    content = b"Local source evidence for unit test"
    (tmp_path / "source.txt").write_bytes(content)

    result = verify_local_document(
        tmp_path,
        document(content),
    )

    assert result["local_byte_integrity"] == "VERIFIED"
    assert result["content_sha256"] == hashlib.sha256(
        content
    ).hexdigest()
    assert result["legal_authenticity"] == "NOT_APPROVED"
    assert result["official_generation"] == "BLOCKED"


def test_tampered_file_rejected(tmp_path):
    original = b"Original evidence"
    (tmp_path / "source.txt").write_bytes(
        b"Modified evidence"
    )

    with pytest.raises(
        D5SourceAdmissionError,
        match="SHA256 differs",
    ):
        verify_local_document(
            tmp_path,
            document(original),
        )


def test_empty_file_rejected(tmp_path):
    (tmp_path / "source.txt").write_bytes(b"")

    with pytest.raises(
        D5SourceAdmissionError,
        match="Empty source",
    ):
        verify_local_document(
            tmp_path,
            document(b"expected"),
        )


def test_missing_file_rejected(tmp_path):
    with pytest.raises(
        D5SourceAdmissionError,
        match="not accessible",
    ):
        verify_local_document(
            tmp_path,
            document(b"missing"),
        )


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", "/tmp/outside.txt", "./source.txt"],
)
def test_unsafe_paths_rejected(tmp_path, path):
    with pytest.raises(D5SourceAdmissionError):
        verify_local_document(
            tmp_path,
            document(b"test", path),
        )


def test_symlink_rejected(tmp_path):
    (tmp_path / "original.txt").write_bytes(b"abc")
    (tmp_path / "source.txt").symlink_to(
        tmp_path / "original.txt"
    )

    with pytest.raises(
        D5SourceAdmissionError,
        match="Symlinked",
    ):
        verify_local_document(
            tmp_path,
            document(b"abc"),
        )


def test_mapping_without_source_rejected():
    with pytest.raises(
        D5SourceAdmissionError,
        match="no documented source",
    ):
        review_field_mapping_claims(
            [],
            [claim()],
        )


def test_mapping_before_document_rejected():
    source = document(b"test")

    earlier = replace(
        claim(),
        effective_from=date(2025, 1, 1),
    )

    with pytest.raises(
        D5SourceAdmissionError,
        match="predates source",
    ):
        review_field_mapping_claims(
            [source],
            [earlier],
        )


def test_mapping_after_document_expiry_rejected():
    original = document(b"test")

    source = replace(
        original,
        source=replace(
            original.source,
            effective_to=date(2026, 6, 30),
        ),
    )

    with pytest.raises(
        D5SourceAdmissionError,
        match="exceeds source validity",
    ):
        review_field_mapping_claims(
            [source],
            [
                replace(
                    claim(),
                    effective_from=date(2026, 7, 1),
                ),
            ],
        )


def test_duplicate_mapping_rejected():
    source = document(b"test")
    item = claim()

    with pytest.raises(
        D5SourceAdmissionError,
        match="Duplicate mapping claim",
    ):
        review_field_mapping_claims(
            [source],
            [item, item],
        )


def test_unknown_requirement_rejected():
    with pytest.raises(
        D5SourceAdmissionError,
        match="Unknown internal",
    ):
        review_field_mapping_claims(
            [document(b"test")],
            [claim("made_up_official_field")],
        )


def test_empty_mapping_inventory_stays_blocked():
    result = review_field_mapping_claims([], [])

    assert result["status"] == "REVIEW_REQUIRED"
    assert result["missing_source_kinds"] == sorted(
        REQUIRED_SOURCE_KINDS
    )
    assert result["missing_mapping_requirements"] == sorted(
        REVIEW_REQUIREMENTS
    )
    assert result["official_generation"] == "BLOCKED"


def test_candidate_mapping_cannot_unblock():
    source = document(b"test")
    result = review_field_mapping_claims(
        [source],
        [claim()],
    )

    assert result["candidate_mapping_count"] == 1
    assert result["legal_source_approval"] is False
    assert result["field_mapping_approval"] is False
    assert result["official_rows_generated"] == 0
    assert result["official_generation"] == "BLOCKED"
