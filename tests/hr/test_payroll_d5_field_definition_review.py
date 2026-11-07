from hashlib import sha256

import pytest

from app.services.payroll_d5_scoped_field_evidence import (
    D5SectionError,
)
from app.services.payroll_d5_field_definition_review import (
    review_d5_field_definitions,
    review_extracted_manifest,
)


DIGEST = sha256(b"source").hexdigest()

TEXT = """
4. Додаток Д1 до Розрахунку ЮО
графа 16 - поле Д1

5. Додаток Д5 до Розрахунку ЮО
графа 04 - порядковий номер
графа 05 - громадянство
графи 13, 14 - код професії та назва
графа 18 - дата створення нового робочого місця

6. Додаток Д6 до Розрахунку ЮО
графа 18 - поле Д6
"""


def review(text=TEXT):
    return review_d5_field_definitions(
        "order243_buhgalter911_full.html",
        DIGEST,
        text,
    )


def test_only_d5_definitions_are_included():
    result = review()
    assert set(result["defined_fields"]) == {
        4, 5, 13, 14, 18
    }
    assert 16 in result["missing_fields"]


def test_combined_fields_are_separate_entries():
    result = review()
    combined = [
        x for x in result["definitions"]
        if x["shared_definition"]
    ]
    assert {x["field_number"] for x in combined} == {
        13, 14
    }


def test_no_normative_approval():
    result = review()
    assert result["legal_approval"] is False
    assert result["normative_mapping_approval"] is False
    assert result["official_generation"] == "BLOCKED"


def test_definition_has_source_provenance():
    result = review()
    for item in result["definitions"]:
        assert item["source_sha256"] == DIGEST
        assert len(item["excerpt_sha256"]) == 64
        assert item["absolute_offset"] >= result["section_start"]


def test_repeated_definition_detected():
    text = TEXT.replace(
        "графа 05 - громадянство",
        "графа 05 - громадянство\n"
        "графа 05 - інше визначення"
    )
    assert 5 in review(text)["duplicate_fields"]


def test_missing_boundary_rejected():
    with pytest.raises(D5SectionError):
        review(
            "5. Додаток Д5 до Розрахунку ЮО "
            "графа 04 - test"
        )


def test_invalid_digest_rejected():
    with pytest.raises(D5SectionError):
        review_d5_field_definitions(
            "source.html", "wrong", TEXT
        )


def test_missing_manifest_source_rejected():
    with pytest.raises(D5SectionError):
        review_extracted_manifest({"sources": []})


def test_manifest_source_review():
    manifest = {
        "sources": [{
            "source": "order243_buhgalter911_full.html",
            "source_sha256": DIGEST,
            "status": "EXTRACTED_REVIEW_ONLY",
            "blocks": [{"text": TEXT}],
        }]
    }
    result = review_extracted_manifest(manifest)
    assert 13 in result["defined_fields"]
    assert result["official_rows_generated"] == 0
