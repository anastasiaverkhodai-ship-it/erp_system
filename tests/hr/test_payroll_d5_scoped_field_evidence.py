from hashlib import sha256

import pytest

from app.services.payroll_d5_scoped_field_evidence import (
    D5SectionError,
    evidence_from_document,
    review_extracted_sources,
    scoped_d5_section,
)


DIGEST = sha256(b"source").hexdigest()

TEXT = """
4. Додаток Д1 до Розрахунку ЮО
Графа 16 додатка Д1.

5. Додаток Д5 до Розрахунку ЮО
Додаток Д5 призначений для обліку кадрових подій.
Графа 12 заповнюється за умовами документа.
Графа 18 використовується у випадках документа.

6. Додаток Д6 до Розрахунку ЮО
Графа 14 додатка Д6.
"""


def test_real_d5_section_excludes_d1_and_d6():
    result = evidence_from_document(
        "source.html", DIGEST, TEXT
    )

    values = {
        item["field_number_candidate"]
        for item in result["field_candidates"]
    }

    assert values == {12, 18}
    assert 16 not in values
    assert 14 not in values


def test_all_candidate_fields_remain_unapproved():
    result = evidence_from_document(
        "source.html", DIGEST, TEXT
    )

    assert result["normative_mapping"] == (
        "NOT_APPROVED"
    )
    assert result["official_generation"] == (
        "BLOCKED"
    )

    assert all(
        item["mapping_approved"] is False
        for item in result["field_candidates"]
    )


def test_source_and_excerpt_provenance():
    result = evidence_from_document(
        "source.html", DIGEST, TEXT
    )

    assert result["source_sha256"] == DIGEST
    assert len(result["section_sha256"]) == 64

    for item in result["field_candidates"]:
        assert len(item["excerpt_sha256"]) == 64
        assert item["absolute_offset"] >= (
            result["section_start"]
        )


def test_missing_d5_heading_rejected():
    with pytest.raises(
        D5SectionError,
        match="Unique D5",
    ):
        scoped_d5_section(
            "Графа 12. Додаток Д1."
        )


def test_missing_d6_end_rejected():
    with pytest.raises(
        D5SectionError,
        match="end boundary",
    ):
        scoped_d5_section(
            "5. Додаток Д5 до Розрахунку ЮО "
            "Графа 12."
        )


def test_duplicate_d5_section_rejected():
    duplicated = TEXT + (
        "\n5. Додаток Д5 до Розрахунку ЮО"
    )

    with pytest.raises(
        D5SectionError,
        match="Unique D5",
    ):
        scoped_d5_section(duplicated)


def test_wrong_hash_format_rejected():
    with pytest.raises(
        D5SectionError,
        match="SHA256",
    ):
        evidence_from_document(
            "source.html", "wrong", TEXT
        )


def test_missing_source_does_not_authorize():
    result = review_extracted_sources(
        {"sources": []}
    )

    assert result["scoped_documents"] == 0
    assert result["blockers"]
    assert result["official_generation"] == (
        "BLOCKED"
    )


def test_d1_only_source_does_not_authorize():
    result = review_extracted_sources({
        "sources": [{
            "source": (
                "order243_buhgalter911_full.html"
            ),
            "status": "EXTRACTED_REVIEW_ONLY",
            "source_sha256": DIGEST,
            "blocks": [{
                "text": (
                    "4. Додаток Д1 до Розрахунку ЮО. "
                    "Графа 16."
                )
            }],
        }]
    })

    assert result["scoped_documents"] == 0
    assert result["blockers"]
    assert result["official_generation"] == (
        "BLOCKED"
    )


def test_complete_candidate_section_stays_blocked():
    result = review_extracted_sources({
        "sources": [{
            "source": (
                "order243_buhgalter911_full.html"
            ),
            "status": "EXTRACTED_REVIEW_ONLY",
            "source_sha256": DIGEST,
            "blocks": [{"text": TEXT}],
        }]
    })

    assert result["scoped_documents"] == 1
    assert result["field_candidates"] == 2
    assert result["legal_approval"] is False
    assert result["field_mapping_approval"] is False
    assert result["official_generation"] == (
        "BLOCKED"
    )
