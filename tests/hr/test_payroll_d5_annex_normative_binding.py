"""Synthetic source binding tests: never normative approval."""

import hashlib
import json
from datetime import date

import pytest

from app.services.payroll_d5_normative_acceptance import (
    D5NormativeEvidenceError,
    D5NormativeSourceCandidate,
    review_d5_annex_normative_bundle,
)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def annex(root, number, when, code, kind):
    content = (
        f"profession_code,change_kind\n{code},{kind}\n"
    ).encode()

    filename = f"annex_{number}.csv"
    manifest = f"manifest_{number}.json"

    (root / filename).write_bytes(content)

    source_hash = digest(
        f"synthetic-source-{number}".encode()
    )

    (root / manifest).write_text(
        json.dumps({
            "amendment_number": number,
            "effective_date": when,
            "source_reference": f"SYNTHETIC-{number}",
            "source_sha256": source_hash,
            "annex_path": filename,
            "annex_sha256": digest(content),
            "document_completeness": "PARTIAL",
            "profession_list_completeness": "PARTIAL",
        }),
        encoding="utf-8",
    )

    return manifest, source_hash


def candidate(number, source_hash, start, end=None):
    return D5NormativeSourceCandidate(
        document_id=f"TEST-DOCUMENT-{number}",
        source_kind="classifier_amendments",
        source_url=f"https://example.org/test/{number}",
        content_sha256=source_hash,
        effective_from=date.fromisoformat(start),
        effective_to=(
            date.fromisoformat(end)
            if end is not None else None
        ),
        review_reference=f"SYNTHETIC-REVIEW-{number}",
    )


def review(root, paths, candidates):
    return review_d5_annex_normative_bundle(
        source_root=root,
        manifest_paths=paths,
        candidates=candidates,
        reporting_date=date(2016, 1, 1),
    )


def test_matching_source_metadata_does_not_approve(tmp_path):
    name, source_hash = annex(
        tmp_path, 2, "2013-01-01", "1234", "introduced",
    )

    result = review(
        tmp_path,
        [name],
        [candidate(2, source_hash, "2012-01-01")],
    )

    assert result["bindings"][0]["status"] == (
        "CANDIDATE_METADATA_MATCH_ONLY"
    )
    assert result["independent_authenticity"] == "NOT_VERIFIED"
    assert result["normative_mapping"] == "NOT_APPROVED"
    assert result["official_generation"] == "BLOCKED"
    assert result["official_rows_generated"] == 0


def test_missing_source_candidate_is_explicit(tmp_path):
    name, _ = annex(
        tmp_path, 2, "2013-01-01", "1234", "introduced",
    )

    result = review(tmp_path, [name], [])

    assert result["missing_binding_amendments"] == (2,)
    assert (
        "D5_AMENDMENT_SOURCE_CANDIDATE_BINDING_MISSING"
        in result["blockers"]
    )


def test_wrong_digest_cannot_bind(tmp_path):
    name, _ = annex(
        tmp_path, 2, "2013-01-01", "1234", "introduced",
    )

    result = review(
        tmp_path,
        [name],
        [candidate(2, digest(b"wrong"), "2012-01-01")],
    )

    assert result["missing_binding_amendments"] == (2,)


def test_out_of_period_source_cannot_bind(tmp_path):
    name, source_hash = annex(
        tmp_path, 2, "2013-01-01", "1234", "introduced",
    )

    result = review(
        tmp_path,
        [name],
        [candidate(2, source_hash, "2014-01-01")],
    )

    assert result["missing_binding_amendments"] == (2,)


def test_duplicate_matching_candidates_are_ambiguous(tmp_path):
    name, source_hash = annex(
        tmp_path, 2, "2013-01-01", "1234", "introduced",
    )

    first = candidate(2, source_hash, "2012-01-01")
    second = D5NormativeSourceCandidate(
        document_id="OTHER-CANDIDATE",
        source_kind=first.source_kind,
        source_url="https://example.org/other",
        content_sha256=first.content_sha256,
        effective_from=first.effective_from,
        effective_to=first.effective_to,
        review_reference="OTHER-REVIEW",
    )

    result = review(tmp_path, [name], [first, second])

    assert result["ambiguous_binding_amendments"] == (2,)
    assert (
        "D5_AMENDMENT_SOURCE_CANDIDATE_BINDING_AMBIGUOUS"
        in result["blockers"]
    )


def test_bundle_digest_independent_of_manifest_order(tmp_path):
    a, a_hash = annex(
        tmp_path, 2, "2013-01-01", "1234", "introduced",
    )
    b, b_hash = annex(
        tmp_path, 3, "2014-01-01", "1234", "removed",
    )

    candidates = [
        candidate(2, a_hash, "2012-01-01", "2013-12-31"),
        candidate(3, b_hash, "2014-01-01"),
    ]

    left = review(tmp_path, [a, b], candidates)
    right = review(tmp_path, [b, a], candidates)

    assert left["bundle_sha256"] == right["bundle_sha256"]
    assert left["amendment_count"] == 2
    assert left["candidate_event_count"] == 2


def test_event_conflicts_remain_visible(tmp_path):
    a, _ = annex(
        tmp_path, 2, "2013-01-01", "1234", "introduced",
    )
    b, _ = annex(
        tmp_path, 3, "2014-01-01", "1234", "introduced",
    )

    result = review(tmp_path, [a, b], [])

    assert result["event_conflict_count"] == 1
    assert (
        "D5_CANDIDATE_EVENT_TRANSITION_CONFLICT"
        in result["blockers"]
    )


def test_tampered_annex_rejected(tmp_path):
    name, _ = annex(
        tmp_path, 2, "2013-01-01", "1234", "introduced",
    )

    (tmp_path / "annex_2.csv").write_bytes(
        b"profession_code,change_kind\n9999,removed\n"
    )

    with pytest.raises(ValueError):
        review(tmp_path, [name], [])


def test_invalid_reporting_date_rejected(tmp_path):
    name, _ = annex(
        tmp_path, 2, "2013-01-01", "1234", "introduced",
    )

    with pytest.raises(D5NormativeEvidenceError):
        review_d5_annex_normative_bundle(
            source_root=tmp_path,
            manifest_paths=[name],
            candidates=[],
            reporting_date="2016-01-01",
        )
