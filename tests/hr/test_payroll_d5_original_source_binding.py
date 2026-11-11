"""Synthetic local source bytes, without legal authentication."""

import hashlib
import json
from datetime import date

import pytest

from app.services.payroll_d5_normative_acceptance import (
    D5NormativeEvidenceError,
    D5NormativeSourceCandidate,
    review_d5_annex_normative_bundle,
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def setup(root):
    source = b"synthetic original source document"
    annex = b"profession_code,change_kind\n1234,introduced\n"

    (root / "source.bin").write_bytes(source)
    (root / "annex.csv").write_bytes(annex)

    manifest = {
        "amendment_number": 2,
        "effective_date": "2013-01-01",
        "source_reference": "SYNTHETIC",
        "source_sha256": sha(source),
        "annex_path": "annex.csv",
        "annex_sha256": sha(annex),
        "document_completeness": "PARTIAL",
        "profession_list_completeness": "PARTIAL",
    }

    (root / "manifest.json").write_text(
        json.dumps(manifest)
    )

    candidate = D5NormativeSourceCandidate(
        document_id="SYNTHETIC-SOURCE",
        source_kind="classifier_amendments",
        source_url="https://example.org/synthetic",
        content_sha256=sha(source),
        effective_from=date(2012, 1, 1),
        effective_to=None,
        review_reference="TEST-ONLY",
    )

    return candidate


def run(root, candidate, documents=None):
    return review_d5_annex_normative_bundle(
        source_root=root,
        manifest_paths=["manifest.json"],
        candidates=[candidate],
        reporting_date=date(2016, 1, 1),
        source_documents=documents,
    )


def test_original_bytes_verified_but_not_approved(tmp_path):
    candidate = setup(tmp_path)
    result = run(tmp_path, candidate, {2: "source.bin"})

    assert result["source_binding"] == (
        "LOCAL_SOURCE_BYTES_MATCH_ONLY"
    )
    assert result["bindings"][0]["status"] == (
        "CANDIDATE_SOURCE_BYTES_MATCH_ONLY"
    )
    assert result["independent_authenticity"] == "NOT_VERIFIED"
    assert result["normative_mapping"] == "NOT_APPROVED"
    assert result["official_generation"] == "BLOCKED"
    assert result["official_rows_generated"] == 0


def test_no_original_bytes_stays_unverified(tmp_path):
    candidate = setup(tmp_path)
    result = run(tmp_path, candidate)

    assert result["unverified_source_amendments"] == [2]
    assert result["source_binding"] == (
        "CANDIDATE_METADATA_ONLY"
    )
    assert "D5_ORIGINAL_SOURCE_BYTES_NOT_VERIFIED" in (
        result["blockers"]
    )


def test_modified_original_rejected(tmp_path):
    candidate = setup(tmp_path)
    (tmp_path / "source.bin").write_bytes(b"modified")

    with pytest.raises(
        D5NormativeEvidenceError,
        match="SHA256 mismatch",
    ):
        run(tmp_path, candidate, {2: "source.bin"})


def test_missing_source_rejected(tmp_path):
    candidate = setup(tmp_path)

    with pytest.raises(D5NormativeEvidenceError):
        run(tmp_path, candidate, {2: "missing.bin"})


def test_path_traversal_rejected(tmp_path):
    candidate = setup(tmp_path)

    with pytest.raises(D5NormativeEvidenceError):
        run(tmp_path, candidate, {2: "../source.bin"})


def test_symlinked_source_rejected(tmp_path):
    candidate = setup(tmp_path)
    (tmp_path / "link.bin").symlink_to("source.bin")

    with pytest.raises(
        D5NormativeEvidenceError,
        match="Symlinked",
    ):
        run(tmp_path, candidate, {2: "link.bin"})


def test_unknown_source_identity_rejected(tmp_path):
    candidate = setup(tmp_path)

    with pytest.raises(D5NormativeEvidenceError):
        run(tmp_path, candidate, {99: "source.bin"})


def test_invalid_source_mapping_rejected(tmp_path):
    candidate = setup(tmp_path)

    with pytest.raises(D5NormativeEvidenceError):
        run(tmp_path, candidate, ["source.bin"])


def test_source_digest_is_deterministic(tmp_path):
    candidate = setup(tmp_path)
    first = run(tmp_path, candidate, {2: "source.bin"})
    second = run(tmp_path, candidate, {2: "source.bin"})

    assert first["bundle_sha256"] == second["bundle_sha256"]


def test_unmodified_existing_metadata_contract(tmp_path):
    candidate = setup(tmp_path)
    result = run(tmp_path, candidate)

    assert result["bindings"][0]["status"] == (
        "CANDIDATE_METADATA_MATCH_ONLY"
    )
    assert result["official_generation"] == "BLOCKED"
