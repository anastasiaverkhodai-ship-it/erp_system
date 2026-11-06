from datetime import date
from hashlib import sha256

import pytest

from app.services.payroll_d5_source_integrity import (
    D5SourceArtifact,
    D5SourceIntegrityError,
    verify_historical_bundle,
    verify_source_artifact,
)


def sample(tmp_path, name="base", content=b"source"):
    path = tmp_path / f"{name}.txt"
    path.write_bytes(content)

    return D5SourceArtifact(
        source_id=name,
        relative_path=path.name,
        sha256_digest=sha256(content).hexdigest(),
        effective_from=date(2010, 11, 1),
    )


def test_valid_source_does_not_grant_approval(tmp_path):
    result = verify_source_artifact(
        sample(tmp_path),
        source_root=tmp_path,
    )

    assert result["sha256_verified"] is True
    assert result["normative_content_verified"] is False
    assert result["official_generation_authorized"] is False


def test_modified_source_is_rejected(tmp_path):
    item = sample(tmp_path)
    (tmp_path / item.relative_path).write_bytes(b"tampered")

    with pytest.raises(
        D5SourceIntegrityError,
        match="SHA256 mismatch",
    ):
        verify_source_artifact(
            item, source_root=tmp_path
        )


def test_path_traversal_rejected(tmp_path):
    item = sample(tmp_path)
    item = D5SourceArtifact(
        source_id=item.source_id,
        relative_path="../outside.txt",
        sha256_digest=item.sha256_digest,
        effective_from=item.effective_from,
    )

    with pytest.raises(
        D5SourceIntegrityError,
        match="Unsafe source path",
    ):
        verify_source_artifact(
            item, source_root=tmp_path
        )


def test_invalid_effective_dates_rejected(tmp_path):
    item = sample(tmp_path)
    invalid = D5SourceArtifact(
        source_id=item.source_id,
        relative_path=item.relative_path,
        sha256_digest=item.sha256_digest,
        effective_from=date(2020, 1, 2),
        effective_to=date(2020, 1, 1),
    )

    with pytest.raises(
        D5SourceIntegrityError,
        match="effective-date",
    ):
        verify_source_artifact(
            invalid, source_root=tmp_path
        )


def test_incomplete_historical_bundle_rejected(tmp_path):
    with pytest.raises(
        D5SourceIntegrityError,
        match="Missing required sources",
    ):
        verify_historical_bundle(
            [sample(tmp_path)],
            source_root=tmp_path,
        )


def test_duplicate_source_rejected(tmp_path):
    item = sample(tmp_path)

    with pytest.raises(
        D5SourceIntegrityError,
        match="Duplicate source IDs",
    ):
        verify_historical_bundle(
            [item, item],
            source_root=tmp_path,
        )


def test_complete_bundle_still_blocks_generation(tmp_path):
    items = [
        sample(tmp_path, name)
        for name in (
            "base",
            "amendment_3",
            "amendment_4",
            "amendment_5",
        )
    ]

    result = verify_historical_bundle(
        items,
        source_root=tmp_path,
    )

    assert result["source_count"] == 4
    assert result["technical_integrity"] == "PASS"
    assert result["independent_normative_approval"] is False
    assert result["official_d5_generation"] == "BLOCKED"


def test_symlink_source_rejected(tmp_path):
    actual = tmp_path / "real.txt"
    actual.write_bytes(b"source")
    link = tmp_path / "link.txt"
    link.symlink_to(actual)

    item = D5SourceArtifact(
        source_id="base",
        relative_path="link.txt",
        sha256_digest=sha256(b"source").hexdigest(),
        effective_from=date(2010, 11, 1),
    )

    with pytest.raises(
        D5SourceIntegrityError,
        match="Symlink",
    ):
        verify_source_artifact(
            item, source_root=tmp_path
        )
