from dataclasses import replace
from datetime import date, datetime

import pytest

from app.services.payroll_d5_normative_acceptance import (
    APPROVED_SOURCE_DOCUMENTS,
    BLOCKER,
    D5NormativeEvidenceError,
    D5NormativeSourceCandidate,
    REQUIRED_SOURCE_KINDS,
    assess_d5_normative_candidates,
)


def candidate(
    kind="profession_classifier",
    document_id="TEST-DOCUMENT",
    start=date(2016, 3, 1),
):
    return D5NormativeSourceCandidate(
        document_id=document_id,
        source_kind=kind,
        source_url="https://example.org/source",
        content_sha256="a" * 64,
        effective_from=start,
        effective_to=None,
        review_reference="TEST-ONLY-NOT-APPROVED",
    )


def test_empty_register_stays_blocked():
    result = assess_d5_normative_candidates()

    assert result.status == "REVIEW_REQUIRED"
    assert result.official_generation == "BLOCKED"
    assert result.official_rows_generated == 0
    assert result.candidate_count == 0
    assert set(result.missing_source_kinds) == (
        REQUIRED_SOURCE_KINDS
    )
    assert BLOCKER in result.blockers


def test_no_approved_sources():
    assert APPROVED_SOURCE_DOCUMENTS == ()


def test_single_candidate_never_approves():
    result = assess_d5_normative_candidates(
        [candidate()]
    )

    assert result.candidate_count == 1
    assert result.official_generation == "BLOCKED"
    assert "d5_field_mapping" in result.missing_source_kinds


def test_complete_candidate_coverage_remains_blocked():
    items = [
        candidate(kind, f"TEST-{number}")
        for number, kind in enumerate(
            sorted(REQUIRED_SOURCE_KINDS),
            1,
        )
    ]

    result = assess_d5_normative_candidates(items)

    assert result.missing_source_kinds == ()
    assert result.official_generation == "BLOCKED"
    assert result.official_rows_generated == 0
    assert BLOCKER in result.blockers


def test_conflicting_effective_dates_are_detected():
    result = assess_d5_normative_candidates([
        candidate(start=date(2016, 3, 1)),
        candidate(start=date(2016, 4, 1)),
    ])

    assert result.conflicting_document_ids == (
        "TEST-DOCUMENT",
    )
    assert "D5_NORMATIVE_SOURCE_CONFLICT" in result.blockers


def test_conflicting_document_hashes_are_detected():
    first = candidate()
    second = replace(
        first,
        content_sha256="b" * 64,
    )

    result = assess_d5_normative_candidates([
        first, second
    ])

    assert result.conflicting_document_ids == (
        "TEST-DOCUMENT",
    )


def test_duplicate_candidate_rejected():
    item = candidate()

    with pytest.raises(
        D5NormativeEvidenceError,
        match="Duplicate evidence candidate",
    ):
        assess_d5_normative_candidates([item, item])


@pytest.mark.parametrize(
    "changes",
    [
        {"document_id": ""},
        {"source_kind": "unverified_kind"},
        {"source_url": "http://example.org/source"},
        {"source_url": "https://user:pass@example.org"},
        {"source_url": "https://example.org/#fragment"},
        {"content_sha256": "bad"},
        {"content_sha256": "0" * 64},
        {"effective_from": datetime(2016, 3, 1)},
        {"effective_to": date(2016, 2, 1)},
        {"review_reference": " "},
    ],
)
def test_invalid_candidate_rejected(changes):
    with pytest.raises(D5NormativeEvidenceError):
        assess_d5_normative_candidates([
            replace(candidate(), **changes)
        ])


def test_untyped_candidate_rejected():
    with pytest.raises(D5NormativeEvidenceError):
        assess_d5_normative_candidates([
            {"document_id": "fake"}
        ])


def test_untyped_collection_rejected():
    with pytest.raises(D5NormativeEvidenceError):
        assess_d5_normative_candidates(None)


def test_large_candidate_set_rejected():
    with pytest.raises(D5NormativeEvidenceError):
        assess_d5_normative_candidates([
            candidate(document_id=str(number))
            for number in range(1001)
        ])
