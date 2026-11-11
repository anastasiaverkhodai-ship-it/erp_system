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



def test_verified_bytes_wrong_candidate_not_fully_bound(tmp_path):
    from dataclasses import replace

    candidate = setup(tmp_path)
    wrong = replace(
        candidate,
        content_sha256=sha(b"different source"),
    )

    result = run(tmp_path, wrong, {2: "source.bin"})

    assert result["bindings"][0]["status"] == (
        "CANDIDATE_SOURCE_BINDING_MISSING"
    )
    assert result["source_binding"] == (
        "SOURCE_BYTES_VERIFIED_BINDING_INCOMPLETE"
    )
    assert (
        "D5_VERIFIED_BYTES_WITHOUT_UNIQUE_SOURCE_BINDING"
        in result["blockers"]
    )
    assert result["official_generation"] == "BLOCKED"


def test_verified_bytes_ambiguous_candidates_not_fully_bound(
    tmp_path,
):
    from dataclasses import replace
    from app.services.payroll_d5_normative_acceptance import (
        review_d5_annex_normative_bundle,
    )

    candidate = setup(tmp_path)
    second = replace(
        candidate,
        document_id="SECOND-SYNTHETIC-SOURCE",
        source_url="https://example.org/second",
        review_reference="SECOND-SYNTHETIC-REVIEW",
    )

    result = review_d5_annex_normative_bundle(
        source_root=tmp_path,
        manifest_paths=["manifest.json"],
        candidates=[candidate, second],
        reporting_date=date(2016, 1, 1),
        source_documents={2: "source.bin"},
    )

    assert result["ambiguous_binding_amendments"] == (2,)
    assert result["source_binding"] == (
        "SOURCE_BYTES_VERIFIED_BINDING_INCOMPLETE"
    )
    assert (
        "D5_VERIFIED_BYTES_WITHOUT_UNIQUE_SOURCE_BINDING"
        in result["blockers"]
    )
    assert result["normative_mapping"] == "NOT_APPROVED"
    assert result["official_rows_generated"] == 0


def test_verified_bytes_unique_candidate_remains_bound(tmp_path):
    candidate = setup(tmp_path)

    result = run(tmp_path, candidate, {2: "source.bin"})

    assert result["source_binding"] == (
        "LOCAL_SOURCE_BYTES_MATCH_ONLY"
    )
    assert result["missing_binding_amendments"] == ()
    assert result["ambiguous_binding_amendments"] == ()
    assert result["independent_authenticity"] == "NOT_VERIFIED"
    assert result["official_generation"] == "BLOCKED"


def test_missing_bytes_cannot_claim_verified_binding(tmp_path):
    candidate = setup(tmp_path)

    result = run(tmp_path, candidate)

    assert result["source_binding"] == (
        "CANDIDATE_METADATA_ONLY"
    )
    assert result["unverified_source_amendments"] == [2]
    assert result["official_generation"] == "BLOCKED"
